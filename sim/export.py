"""Export des données du dashboard (docs/data/*.json), lues par la PWA statique."""
import shutil
from pathlib import Path

from . import market_calendar as mc
from .decide import horizon
from .journal import Journal, read_json, write_json
from .knowledge import Knowledge
from .usage import Usage, summarize
from .ledger import Ledger

LABELS = {"agents": "Équipe d'agents", "agents_lowcost": "Agents, grille low-cost",
          "benchmark": "ETF Monde (buy & hold)", "random": "Aléatoire (contrôle)"}


def _f(x):
    return None if x is None else float(x)


def _thesis_lookup(journal: Journal) -> dict:
    """(date de décision, ticker, sens) -> ordre final du journal."""
    out = {}
    for d in journal.trading_days():
        for o in (journal.read_day(d) or {}).get("final_orders", []):
            out[(d, o["ticker"], o["side"])] = o
    return out


# Les agents de l'équipe : clé d'appel LLM, nom, préfixes des messages d'erreur, rôle
LLM_AGENTS = [
    ("market", "Analyste marché", ("analyste marché",), "Lit le screener (cours, volumes, RSI) et repère des opportunités"),
    ("news", "Analyste actualité", ("analyste actualité",), "Lit la presse, tient les notes et l'agenda, juge si c'est « déjà dans le prix »"),
    ("chef", "Chef", ("chef",), "Décide les ordres, cite ses sources, chiffre ses prédictions"),
    ("risk", "Gestionnaire de risque", ("gestionnaire de risque",), "Peut bloquer un ordre déraisonnable (seulement s'il y a des ordres)"),
    ("review", "Revue quotidienne", ("revue quotidienne",), "Compare les décisions de la veille à la réalité, propose des règles"),
    ("dossier", "Dossiers entreprises", ("dossiers", "historique"), "Constitue l'historique 6 mois des titres suivis (une fois par titre)"),
    ("postmortem", "Post-mortems", ("post-mortems",), "Analyse chaque position clôturée (quand il y en a)"),
    ("weekly", "Revue hebdomadaire", ("revue hebdomadaire",), "Fait le ménage du playbook, une fois par semaine (week-end)"),
    ("research", "Recherche web", ("recherche web",), "Samedi : lit de vraies pages (calendrier, résultats, thèmes) avec sources"),
    ("plan", "Plan de la semaine", ("plan de la semaine",), "Dimanche : le chef prépare des scénarios « si X alors Y » pour lundi"),
    ("report", "Rapport final", ("rapport final",), "Rédige l'analyse de fin de simulation (dernier jour)"),
]
CODE_AGENTS = [
    ("ledger", "Comptabilité", "Positions, frais, P&L, valorisation, benchmark (code, pas de LLM)"),
    ("screener", "Screener", "Indicateurs sur les titres liquides (code, pas de LLM)"),
    ("validator", "Validation des ordres", "PEA, liquidité, cash, 40 %, 5 ordres/jour (code, pas de LLM)"),
]


def agent_states(e: dict, warnings=()) -> dict[str, dict]:
    """État de chaque agent pour un run : ok / error / skipped (non requis ce jour-là)."""
    calls, msgs = e.get("llm_calls", []), [*e.get("errors", []), *warnings]
    saved = {s["role"]: s["reason"] for s in e.get("budget_skips", [])}      # appels économisés (budget)
    out = {}
    for key, _, labels, _ in LLM_AGENTS:
        mine = [c for c in calls if c["role"] == key]
        errs = [m for m in msgs if m.startswith(labels)]
        status = "error" if errs else "ok" if mine else "budget" if key in saved else "skipped"
        if status == "skipped" and key == "postmortem" and (e.get("learning") or {}).get("postmortems"):
            status = "ok"                                   # appel LLM non journalisé, mais post-mortem écrit
        if status == "skipped" and key == "report" and e.get("final"):
            status = "ok"
        out[key] = {"status": status, "calls": len(mine), "seconds": round(sum(c["duration_s"] for c in mine)),
                    "tokens_in": sum(c["input_tokens"] for c in mine), "tokens_out": sum(c["output_tokens"] for c in mine),
                    "model": mine[0]["model"] if mine else None,
                    "error": errs[0][:200] if errs else None, "note": saved.get(key)}
    trading = e.get("mode") == "trading"
    for key, ran in (("ledger", trading and "portfolio" in e), ("screener", trading and "screener" in e),
                     ("validator", trading and "screener" in e)):
        out[key] = {"status": "ok" if ran else "skipped"}
    return out


def export_agents(cfg: dict, root: Path, warnings=()) -> None:
    """Tableau des agents du dashboard : dernier run et 7 derniers runs. `warnings` = ceux du run en cours."""
    jdir = root / cfg["simulation"]["journal_dir"]
    entries = sorted((read_json(p) for p in jdir.glob("????-??-??*.json")), key=lambda e: e.get("run_at", ""))
    if not entries:
        return
    last, recent = entries[-1], entries[-7:]
    states = agent_states(last, warnings)
    history = [{"date": e["date"], "mode": e["mode"], "states": {k: v["status"] for k, v in
                (agent_states(e, warnings if e is last else ()).items())}} for e in recent]
    agents = []
    for key, name, _, desc in LLM_AGENTS:
        agents.append({"key": key, "name": name, "kind": "llm", "desc": desc, **states[key],
                       "model": states[key]["model"] or cfg["agents"]["models"].get(key)})
    for key, name, desc in CODE_AGENTS:
        agents.append({"key": key, "name": name, "kind": "code", "desc": desc, **states[key], "model": None})
    write_json(root / "docs" / "data" / "agents.json",
               {"last_run": {"date": last["date"], "mode": last["mode"], "run_at": last.get("run_at")},
                "agents": agents, "history": history})


def export_usage(cfg: dict, root: Path) -> None:
    """Consommation de l'abonnement (jour, semaine, 14 jours, par agent, quotas atteints) pour le dashboard."""
    b = cfg["budget"]
    usage = Usage(root / b["log"], root / b["shared_log"] if b.get("shared_log") else None)
    write_json(root / "docs" / "data" / "usage.json", summarize(cfg, usage))


def export_knowledge(cfg: dict, root: Path, out: Path, as_of: str) -> None:
    """Playbook (avec effet mesuré des règles), journal des changements, prédictions, calibration, notes, agenda."""
    kb = Knowledge(root / cfg["knowledge"]["dir"])
    pb = kb.playbook()
    effects = kb.rule_effects()
    for r in pb["rules"]:
        r["effect"] = effects.get(r["id"])
    agenda = [e for e in kb.agenda() if not e["date"] or e["date"] >= as_of]
    write_json(out / "knowledge.json", {
        "playbook": pb["rules"], "log": read_json(kb.root / "playbook_log.json", [])[-200:],
        "calibration": kb.calibration(), "predictions": kb.predictions()["items"][-150:],
        "insights": kb.insights(), "notes": kb.notes(), "agenda": sorted(agenda, key=lambda e: e["date"] or "9999"),
        "weekplan": kb.weekplan(), "research": kb.research(),
    })


def export_dashboard(cfg: dict, root: Path, ledgers: dict[str, Ledger], journal: Journal, store, as_of: str,
                     universe: dict | None = None) -> Path:
    out = root / "docs" / "data"
    (out / "journal").mkdir(parents=True, exist_ok=True)
    universe = universe or {}
    agents = ledgers["agents"]

    books = {}
    for b, led in ledgers.items():
        st = led.state()
        books[b] = {"label": LABELS[b], "series": [{"date": d, "total": _f(t)} for d, t in led.daily_values()],
                    "cash": _f(st.cash), "fees": _f(st.fees_paid), "dividends": _f(st.dividends),
                    "realized": _f(st.realized_pnl), "deposits": _f(st.deposits), "n_fills": len(led.fills()),
                    "positions": sorted(st.positions)}

    st = agents.state()
    marks = store.marks(list(st.positions), as_of)
    total = st.total_value(marks) if marks or not st.positions else None
    positions = []
    for t, p in st.positions.items():
        m = marks.get(t)
        positions.append({"ticker": t, "name": getattr(universe.get(t), "name", t), "qty": p.qty,
                          "pru": _f(round(p.pru, 4)), "price": _f(m), "value": _f(m * p.qty) if m else None,
                          "weight": _f(round(m * p.qty / total * 100, 1)) if m and total else None,
                          "pnl_pct": _f(round((m / p.pru - 1) * 100, 2)) if m else None})

    h = horizon(agents, as_of, cfg) if agents.start_date() else None
    write_json(out / "summary.json", {
        "as_of": as_of, "horizon": h, "capital": cfg["capital_initial"],
        "total_sessions": len(mc.sessions_between(h["start"], h["end"])) if h else None,
        "fee_grid": cfg["fees"]["active"], "compare_grid": cfg["simulation"]["compare_grid"],
        "books": books, "positions": positions, "pending": journal.load_pending(),
    })

    theses = _thesis_lookup(journal)
    fills = []
    for f in agents.fills():
        o = theses.get((mc.previous_session(f.date), f.ticker, f.side), {})
        fills.append({"date": f.date, "ticker": f.ticker, "side": f.side, "qty": f.qty, "price": _f(f.price),
                      "ref_price": _f(f.ref_price), "fees": _f(f.fees), "amount": _f(f.amount),
                      "thesis": o.get("thesis"), "exit_plan": o.get("exit_plan")})
    write_json(out / "orders.json", {
        "fills": fills, "rejections": agents.rejections(),
        "others": {b: [{"date": f.date, "ticker": f.ticker, "side": f.side, "qty": f.qty, "price": _f(f.price),
                        "fees": _f(f.fees)} for f in led.fills()] for b, led in ledgers.items() if b != "agents"},
    })

    index = []
    for p in sorted(journal.root.glob("????-??-??*.json")):
        e = read_json(p)
        shutil.copyfile(p, out / "journal" / p.name)
        index.append({"file": p.name, "date": e["date"], "mode": e["mode"], "final": e.get("final", False),
                      "n_orders": len(e.get("final_orders", [])), "n_refused": len(e.get("refused", [])),
                      "n_exec": sum(1 for x in e.get("executions", []) if x.get("filled")),
                      "n_errors": len(e.get("errors", [])),
                      "n_rule_changes": len((e.get("learning") or {}).get("playbook_changes", [])),
                      "view": ((e.get("chef") or {}).get("market_view") or
                               (e.get("news_analysis") or {}).get("summary") or "")[:160]})
    write_json(out / "journal" / "index.json", sorted(index, key=lambda x: x["file"], reverse=True))
    write_json(out / "postmortems.json", journal.postmortems())
    export_knowledge(cfg, root, out, as_of)
    export_usage(cfg, root)
    report = journal.root / "rapport-final.json"
    if report.exists():
        shutil.copyfile(report, out / "report.json")
    return out
