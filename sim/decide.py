"""Runs LLM d'une journée.

run_decision (soir de séance) :
  1. évaluation des prédictions arrivées à échéance (calibration)
  2. revue quotidienne : décisions de la veille vs ce qui s'est passé → playbook, connaissances
  3. dossiers 6 mois des titres suivis qui n'en ont pas encore
  4. analyste marché, analyste actualité (notes, agenda), chef (sources obligatoires, prédictions, scénarios),
     validation automatique, gestionnaire de risque → ordres en attente
  5. enregistrement des prédictions et de l'apprentissage du jour dans le journal
run_veille (week-end / férié) : analyste actualité (notes, agenda) + revue hebdomadaire si elle est due
run_postmortems : post-mortem de chaque position clôturée (thèse, réalité, prévisible ?, règle)

Une panne LLM ne fait jamais échouer le run : elle est notée dans entry["errors"], et la sécurité prime
(chef indisponible → aucun ordre ; risque indisponible → tous les ordres bloqués).
"""
import copy
import json
from datetime import date, timedelta

from . import agents
from . import market_calendar as mc
from .broker import plan_orders
from .config import fee_grid
from .dossier import build_dossiers, fetch_history
from .journal import Journal, digest, read_json, write_json
from .knowledge import Knowledge
from .ledger import Ledger, apply_fill, round_trips, summary
from .llm import LLMError, claude_cli
from .models import Order, PortfolioState
from .money import D, cents
from .news import NewsItem, format_news, price_reactions
from .screener import breadth, format_table, screen, select


# --- Mise en forme des données pour les agents --------------------------------

def horizon(ledger: Ledger, as_of: str, cfg: dict) -> dict:
    start = ledger.start_date() or as_of
    end = (date.fromisoformat(start) + timedelta(days=cfg["simulation"]["duration_days"])).isoformat()
    nxt = mc.next_session(as_of)
    return {"start": start, "end": end, "exec_date": nxt,
            "session_no": len(mc.sessions_between(start, as_of)),
            "sessions_left": len(mc.sessions_between(nxt, end)) if nxt <= end else 0}


def horizon_text(h: dict, as_of: str) -> str:
    return (f"Décision du {as_of} (après clôture). Ordres exécutés à l'ouverture du {h['exec_date']}. "
            f"Simulation du {h['start']} au {h['end']} : séance n° {h['session_no']}, "
            f"il restera {h['sessions_left']} séance(s) de cotation à partir de la prochaine exécution.")


def portfolio_text(state: PortfolioState, marks: dict) -> str:
    total = state.total_value(marks)
    lines = [f"Cash : {state.cash} € | Valeur totale : {cents(total)} € | Dépôts : {state.deposits} € | "
             f"Frais payés : {state.fees_paid} € | P&L réalisé : {state.realized_pnl} €"]
    if not state.positions:
        lines.append("Aucune position.")
    for t, p in state.positions.items():
        m = D(marks[t])
        value = m * p.qty
        lines.append(f"- {t} : {p.qty} actions, PRU {p.pru:.4f} €, cours {m} €, valeur {cents(value)} € "
                     f"({value / total * 100:.1f} % du portefeuille), latent {(m / p.pru - 1) * 100:+.2f} %")
    return "\n".join(lines)


def performance_text(agents_summary: dict, bench_summary: dict | None) -> str:
    s = f"Équipe : {cents(agents_summary['total'])} € ({agents_summary['return_pct']:.2f} %)"
    if bench_summary:
        s += f" | Benchmark ETF Monde : {cents(bench_summary['total'])} € ({bench_summary['return_pct']:.2f} %)"
    return s


def _json_text(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1) if obj else "(indisponible)"


def lessons_text(journal: Journal, n: int) -> str:
    pms = journal.postmortems()[-n:]
    return "\n".join(f"- {p['trade_id']} ({p['pnl_pct']} %) : {p['lesson']}"
                     + (f" | prévisible ? {p['predictable']}" if p.get("predictable") else "")
                     for p in pms) or "(aucun pour l'instant)"


def _call(entry: dict, role: str, fn):
    try:
        obj, metas = fn()
        entry["llm_calls"] += metas
        return obj
    except LLMError as e:
        entry["errors"].append(f"{role} : {e}")
        return None


def _jsonable(d: dict) -> dict:
    return {k: (str(v) if not isinstance(v, (str, int, float, bool, list, dict, type(None))) else v)
            for k, v in d.items()}


def nth_session(start: str, n: int) -> str:
    """n-ième séance en comptant `start` comme la première."""
    d = start
    for _ in range(n - 1):
        d = mc.next_session(d)
    return d


def _learning(entry: dict) -> dict:
    return entry.setdefault("learning", {"playbook_changes": [], "predictions_resolved": [], "predictions_added": [],
                                         "notes_updated": [], "insights_added": 0, "agenda_added": 0,
                                         "dossiers_built": []})


def _apply_ops(entry, kb: Knowledge, ops, day, by, cfg) -> None:
    _learning(entry)["playbook_changes"] += kb.apply_playbook_ops(ops or [], day, by, cfg)


# --- Revue quotidienne -----------------------------------------------------------------

def _ret(store, t, d0, d1):
    a, b = store.last_close(t, d0), store.last_close(t, d1)
    return f"{(b / a - 1) * 100:+.2f} %" if a and b else "n/d"


def run_daily_review(entry, *, as_of, cfg, store, journal: Journal, kb: Knowledge, resolved, executions, llm):
    prev = journal.recent(as_of, 1)
    if not prev or not prev[0].get("chef"):
        return None
    y = prev[0]
    d0 = y["date"]
    lines = [digest(y)]
    for o in y.get("final_orders", []):
        p = o.get("prediction")
        if p:
            lines.append(f"- prédiction {o['ticker']} : {p['direction']} ≥ {p['move_pct']} % en {p['horizon_sessions']} "
                         f"séances, proba {p['probability']}")
    tickers = {o["ticker"] for o in y.get("final_orders", []) + y.get("refused", [])}
    tickers |= {o["ticker"] for o in (y.get("market_analysis") or {}).get("opportunities", [])}
    tickers |= {c["ticker"] for c in (y.get("news_analysis") or {}).get("by_company", []) if c.get("ticker")}
    bench = cfg["benchmark"]["ticker"]
    out = [f"Benchmark {bench} : {_ret(store, bench, d0, as_of)} entre la clôture du {d0} et celle du {as_of}."]
    out += [f"- {t} : {_ret(store, t, d0, as_of)}" for t in sorted(tickers) if store.last_close(t, as_of)]
    out += [f"- exécution {e['side']} {e['ticker']} : " + (f"{e['qty']} à {e['price']} €" if e["filled"]
                                                            else f"non exécuté ({e['reason']})") for e in executions]
    res = "\n".join(f"- {p['id']} {p['ticker']} ({p['thesis'][:120]}) : {'RÉUSSIE' if p['hit'] else 'ratée'}, "
                    f"{p['return_pct']:+.2f} % (règles {', '.join(p.get('rules', [])) or 'aucune'})"
                    for p in resolved) or "(aucune)"
    review = _call(entry, "revue quotidienne", lambda: agents.daily_reviewer(
        cfg, yesterday="\n".join(lines), outcomes="\n".join(out), resolved=res, playbook=kb.render_playbook(),
        insights=kb.render_insights(), llm=llm))
    if review:
        _apply_ops(entry, kb, review["playbook_ops"], as_of, "revue quotidienne", cfg)
        _learning(entry)["insights_added"] += kb.add_insights(review["insights"], as_of, "revue quotidienne")
    return review


# --- Run de décision ------------------------------------------------------------

def run_decision(*, as_of: str, run_at, cfg: dict, store, instruments: dict, ledger: Ledger,
                 journal: Journal, news_items: list[NewsItem], kb: Knowledge, watch: dict[str, str] | None = None,
                 bench_summary: dict | None = None, executions: list[dict] | None = None, llm=claude_cli,
                 fetch_hist=fetch_history) -> dict:
    grid = fee_grid(cfg)
    state = ledger.state()
    held = sorted(state.positions)
    marks = store.marks(held, as_of)
    rows = screen(store, instruments, as_of, cfg, held)
    table = select(rows, cfg, held, state, marks, grid)
    h = horizon(ledger, as_of, cfg)
    agents_summary = summary(state, marks)
    watch = watch or {}

    entry = {"date": as_of, "mode": "trading", "run_at": run_at.isoformat(), "exec_date": h["exec_date"],
             "horizon": h, "errors": [], "llm_calls": [], "executions": executions or [], "watch": watch,
             "portfolio": _jsonable(agents_summary), "benchmark": _jsonable(bench_summary or {}),
             "screener": {"breadth": breadth(rows), "rows": table},
             "news": [i.__dict__ for i in news_items]}
    learn = _learning(entry)

    # 1-2. Évaluer les prédictions échues, puis revue de la veille
    resolved = kb.resolve_predictions(store, as_of, cfg["benchmark"]["ticker"])
    learn["predictions_resolved"] = resolved
    entry["daily_review"] = run_daily_review(entry, as_of=as_of, cfg=cfg, store=store, journal=journal, kb=kb,
                                             resolved=resolved, executions=entry["executions"], llm=llm)

    # 3. Dossiers 6 mois des titres suivis
    try:
        built, metas, errs = build_dossiers(cfg, kb, {t: watch[t] for t in watch}, as_of, llm=llm, fetch=fetch_hist)
        learn["dossiers_built"], entry["llm_calls"] = built, entry["llm_calls"] + metas
        entry["errors"] += errs
    except LLMError as e:
        entry["errors"].append(f"dossiers : {e}")

    playbook = kb.render_playbook()
    agenda = kb.render_agenda(as_of)
    focus = list(dict.fromkeys([*held, *watch]))
    txt = {"horizon": horizon_text(h, as_of), "portfolio": portfolio_text(state, marks),
           "screener": format_table(table), "lessons": lessons_text(journal, cfg["agents"]["postmortems"]),
           "calibration": kb.render_calibration()}

    # 4. Analystes
    market = _call(entry, "analyste marché", lambda: agents.market_analyst(
        cfg, horizon=txt["horizon"], portfolio=txt["portfolio"], breadth=_json_text(entry["screener"]["breadth"]),
        screener=txt["screener"], playbook=playbook, agenda=agenda, llm=llm))
    reactions = price_reactions(news_items, store, as_of)
    news = _call(entry, "analyste actualité", lambda: agents.news_analyst(
        cfg, horizon=txt["horizon"], portfolio=txt["portfolio"], playbook=playbook,
        notes=kb.render_notes(focus), macro_notes=journal.context_notes(), agenda=agenda,
        insights=kb.render_insights(), news=format_news(news_items, reactions), llm=llm))
    entry["market_analysis"], entry["news_analysis"] = market, news
    if news:
        journal.set_context_notes(news["context_notes"])
        learn["notes_updated"] = kb.update_notes(news["notes_updates"], as_of, "analyste actualité")
        learn["agenda_added"] += kb.update_agenda(news["upcoming_events"], as_of, "analyste actualité")

    # Chef
    recent = journal.recent(as_of, cfg["agents"]["journal_days"])
    known = {i.id for i in news_items} | set(kb.note_keys()) | {"screener"}
    rule_ids = {r["id"] for r in kb.active_rules()}
    chef = _call(entry, "chef", lambda: agents.chef(
        cfg, horizon=txt["horizon"], portfolio=txt["portfolio"],
        performance=performance_text(agents_summary, bench_summary), market=_json_text(market),
        news=_json_text(news), notes=kb.render_notes(focus), screener=txt["screener"],
        journal="\n\n".join(digest(e) for e in recent) or "(premier jour)", lessons=txt["lessons"],
        playbook=kb.render_playbook(), calibration=txt["calibration"], open_predictions=kb.open_predictions_text(),
        agenda=kb.render_agenda(as_of), insights=kb.render_insights(), known_sources=known, rule_ids=rule_ids,
        llm=llm))
    entry["chef"] = chef

    final, refused = [], []
    if chef and chef["orders"]:
        final, refused = _validate_and_review(entry, chef, state, instruments, store, marks, cfg, grid,
                                              as_of, txt, market, news, ledger, kb, llm)
    entry["final_orders"], entry["refused"] = final, refused

    # 5. Prédictions et scénarios
    if chef:
        learn["predictions_added"] = _record_predictions(kb, chef, final, refused, store, as_of, h["exec_date"])
        learn["agenda_added"] += kb.update_agenda(chef.get("scenarios", []), as_of, "chef")
    journal.save_pending(as_of, h["exec_date"], final)
    journal.write_day(entry)
    return entry


def _record_predictions(kb: Knowledge, chef, final, refused, store, as_of, exec_date) -> list[dict]:
    preds = []

    def add(ticker, kind, o, catalysts):
        p, ref = o.get("prediction"), store.last_close(ticker, as_of)
        if not p or ref is None:
            return
        preds.append({"date": as_of, "ticker": ticker, "kind": kind, "thesis": o["thesis"],
                      "direction": p["direction"], "move_pct": p["move_pct"],
                      "horizon_sessions": p["horizon_sessions"], "probability": p["probability"],
                      "target_date": nth_session(exec_date, p["horizon_sessions"]), "ref_price": float(ref),
                      "rules": o.get("rules_applied", []), "catalysts": catalysts})

    for o in final:
        add(o["ticker"], f"ordre {o['side']}", o, o["sources"])
    for o in refused:
        add(o["ticker"].strip().upper(), f"ordre {o['side']} refusé ({o['by']})", o,
            [c["source_id"] for c in o.get("catalysts", [])])
    for w in chef.get("watch_predictions", []):
        add(w["ticker"].strip().upper(), "surveillance", w, w.get("catalysts", []))
    return kb.add_predictions(preds)


def _validate_and_review(entry, chef, state, instruments, store, marks, cfg, grid, as_of, txt,
                         market, news, ledger, kb: Knowledge, llm):
    by_obj, orders = {}, []
    for o in chef["orders"]:
        order = Order(o["ticker"].strip().upper(), o["side"], o["qty"], o["thesis"])
        orders.append(order)
        by_obj[id(order)] = o
    closes = {o.ticker: p for o in orders if (p := store.price(o.ticker, as_of)) is not None}
    outcomes = plan_orders(orders, state, instruments, closes, marks, cfg, grid, as_of,
                           allow_reduce=cfg["execution"]["reduce_qty_if_needed"])

    refused, accepted = [], []
    for out in outcomes:
        o = by_obj[id(out.order)]
        if out.fill is None:
            refused.append({**o, "by": "validation", "reason": out.verdict.reason})
            ledger.record_rejection(as_of, "decision", out.order, out.verdict.reason)
        else:
            accepted.append((out, o))
    if not accepted:
        return [], refused

    # Poids estimés après exécution de tous les ordres acceptés (au cours de clôture)
    after = copy.deepcopy(state)
    for out, _ in accepted:
        apply_fill(after, out.fill)
    m_after = {**marks, **closes}
    total_after = after.total_value(m_after)
    lines = []
    for i, (out, o) in enumerate(accepted):
        f = out.fill
        weight = D(m_after[f.ticker]) * after.qty(f.ticker) / total_after * 100 if total_after else 0
        note = f" [{out.verdict.reason}]" if out.verdict.reason else ""
        cats = "; ".join(f"[{c['source_id']}] {c['title']} ({c['date']}) → {c['link']}" for c in o["catalysts"])
        p = o["prediction"]
        lines.append(f"[{i}] {f.side} {f.qty} {f.ticker}{note} ≈ {f.amount} € + frais {f.fees} € ; "
                     f"poids après ≈ {weight:.1f} % ; thèse : {o['thesis']} ; sources : {cats} ; "
                     f"règles : {', '.join(o['rules_applied']) or 'aucune'} ; prédiction : {p['direction']} "
                     f"≥ {p['move_pct']} % en {p['horizon_sessions']} séances, proba {p['probability']} ; "
                     f"sortie : {o['exit_plan']}")
    refused_txt = "\n".join(f"- {r['side']} {r['ticker']} : {r['reason']}" for r in refused) or "(aucun)"

    risk = _call(entry, "gestionnaire de risque", lambda: agents.risk_manager(
        cfg, n_orders=len(accepted), horizon=txt["horizon"], portfolio=txt["portfolio"], orders="\n".join(lines),
        refused=refused_txt, market=_json_text(market), news=_json_text(news), lessons=txt["lessons"],
        playbook=kb.render_playbook(), calibration=txt["calibration"], llm=llm))
    entry["risk"] = risk
    decisions = {r["index"]: r for r in risk["reviews"]} if risk else {}

    final = []
    for i, (out, o) in enumerate(accepted):
        review = decisions.get(i, {"decision": "BLOCK", "reason": "gestionnaire de risque indisponible"})
        if review["decision"] == "BLOCK":
            refused.append({**o, "by": "risque", "reason": review["reason"]})
            ledger.record_rejection(as_of, "risk", out.order, review["reason"])
        else:
            f = out.fill
            final.append({"ticker": f.ticker, "side": f.side, "qty": f.qty, "est_price": str(f.price),
                          "est_fees": str(f.fees), "thesis": o["thesis"], "catalysts": o["catalysts"],
                          "sources": [c["source_id"] for c in o["catalysts"]], "exit_plan": o["exit_plan"],
                          "rules_applied": o["rules_applied"], "prediction": o["prediction"],
                          "validation_note": out.verdict.reason, "risk_note": review["reason"]})
    return final, refused


# --- Veille et revue hebdomadaire ------------------------------------------------------

def weekly_due(kb: Knowledge, today: str, cfg: dict) -> bool:
    last = (read_json(kb.root / "meta.json", {}) or {}).get("last_weekly")
    return last is None or (date.fromisoformat(today) - date.fromisoformat(last)).days >= cfg["knowledge"]["weekly_review_days"]


def run_weekly_review(entry, *, today, cfg, journal: Journal, kb: Knowledge, llm) -> dict | None:
    days = [journal.read_day(d) for d in journal.trading_days()
            if d >= (date.fromisoformat(today) - timedelta(days=8)).isoformat()]
    if not days:
        return None
    pms = [p for p in journal.postmortems() if p.get("written_on", "") >= days[0]["date"]]
    eff = kb.rule_effects()
    calib = kb.render_calibration() + "\nEffet des règles : " + (
        "; ".join(f"{r} appliquée {e['n']} fois, {e['hits']} réussites, rendement moyen {e['avg_return']} %"
                  for r, e in eff.items()) or "aucune règle encore évaluée")
    full = "\n\n".join(f"[{r['id']}] {r['status']} / {r['confidence']} : {r['text']}\n  cas : "
                       + "; ".join(f"{e['date']} {e['kind']} ({e['origin']}) {e['note'][:100]}" for e in r["evidence"])
                       for r in kb.playbook()["rules"] if r["status"] == "active") or "(vide)"
    notes = "\n".join(f"- {k} : {n['text'][:300]}" for k, n in kb.notes().items() if n["kind"] != "entreprise")
    review = _call(entry, "revue hebdomadaire", lambda: agents.weekly_reviewer(
        cfg, week="\n\n".join(digest(e) for e in days), postmortems=_json_text(pms), calibration=calib,
        playbook=full, insights=kb.render_insights(60), notes=notes or "(aucune)", llm=llm))
    if review:
        learn = _learning(entry)
        _apply_ops(entry, kb, review["playbook_ops"], today, "revue hebdomadaire", cfg)
        learn["insights_added"] += kb.add_insights(review["insights"], today, "revue hebdomadaire",
                                                   obsolete_ids=set(review["obsolete_insights"]))
        learn["notes_updated"] += kb.update_notes(review["notes_updates"], today, "revue hebdomadaire")
        write_json(kb.root / f"weekly/{today}.json", review)
        write_json(kb.root / "meta.json", {**(read_json(kb.root / "meta.json", {}) or {}), "last_weekly": today})
    return review


def run_veille(*, today: str, run_at, cfg: dict, ledger: Ledger, store, journal: Journal,
               news_items: list[NewsItem], kb: Knowledge, watch: dict[str, str] | None = None,
               llm=claude_cli) -> dict:
    state = ledger.state()
    last = mc.previous_session(today) if not mc.is_trading_day(today) else today
    marks = store.marks(list(state.positions), last)
    entry = {"date": today, "mode": "veille", "run_at": run_at.isoformat(), "errors": [], "llm_calls": [],
             "news": [i.__dict__ for i in news_items]}
    learn = _learning(entry)
    focus = list(dict.fromkeys([*state.positions, *(watch or {})]))
    news = _call(entry, "analyste actualité", lambda: agents.news_analyst(
        cfg, horizon=f"Jour sans cotation ({today}) : veille uniquement, aucun ordre. Prépare la semaine.",
        portfolio=portfolio_text(state, marks), playbook=kb.render_playbook(), notes=kb.render_notes(focus),
        macro_notes=journal.context_notes(), agenda=kb.render_agenda(today), insights=kb.render_insights(),
        news=format_news(news_items, price_reactions(news_items, store, last)), llm=llm))
    entry["news_analysis"] = news
    if news:
        journal.set_context_notes(news["context_notes"])
        learn["notes_updated"] = kb.update_notes(news["notes_updates"], today, "analyste actualité (veille)")
        learn["agenda_added"] += kb.update_agenda(news["upcoming_events"], today, "analyste actualité (veille)")
    if weekly_due(kb, today, cfg):
        entry["weekly_review"] = run_weekly_review(entry, today=today, cfg=cfg, journal=journal, kb=kb, llm=llm)
    journal.write_day(entry)
    return entry


# --- Post-mortems -------------------------------------------------------------------

def run_postmortems(*, as_of: str, cfg: dict, ledger: Ledger, store, journal: Journal, kb: Knowledge,
                    llm=claude_cli) -> tuple[list[dict], list[dict]]:
    """Renvoie (post-mortems écrits, changements du playbook)."""
    done = {p["trade_id"] for p in journal.postmortems()}
    trips = [t for t in round_trips(ledger.fills()) if t["closed"] and t["trade_id"] not in done]
    if not trips:
        return [], []

    bench = cfg["benchmark"]["ticker"]
    blocks = []
    for t in trips:
        b0, b1 = store.price(bench, t["open_date"]), store.price(bench, t["close_date"])
        t["benchmark_pct"] = str(round((b1 / b0 - 1) * 100, 2)) if b0 and b1 else None
        theses = []
        for e in journal.recent(mc.next_session(t["close_date"]), 60):
            if not (mc.previous_session(t["open_date"]) <= e["date"] < t["close_date"]):
                continue
            for o in e.get("final_orders", []):
                if o["ticker"] != t["ticker"]:
                    continue
                cats = "; ".join(f"{c['title']} ({c['date']})" for c in o.get("catalysts", []))
                p = o.get("prediction")
                pred = f" ; prédiction {p['direction']} ≥ {p['move_pct']} % en {p['horizon_sessions']} séances, " \
                       f"proba {p['probability']}" if p else ""
                theses.append(f"{e['date']} {o['side']} : {o['thesis']} (sources : {cats or 'n/d'} ; "
                              f"sortie prévue : {o['exit_plan']}{pred})")
        days = len(mc.sessions_between(t["open_date"], t["close_date"]))
        blocks.append(
            f"### trade_id {t['trade_id']}\n"
            f"Achat moyen {t['avg_buy']} € ({t['qty_bought']} actions) le {t['open_date']}, "
            f"vente moyenne {t['avg_sell']} € le {t['close_date']} ({days} séances). "
            f"Frais {t['fees']} €. Résultat net {t['pnl']} € ({t['pnl_pct']} %). "
            f"Benchmark sur la même période : {t['benchmark_pct']} %.\nThèses du chef :\n"
            + ("\n".join(f"- {x}" for x in theses) or "- (non retrouvées dans le journal)"))
    # En cas d'échec LLM, l'exception remonte au run quotidien (notification) ; rien n'est enregistré,
    # donc ces post-mortems seront retentés au run suivant.
    obj, _ = agents.postmortem_writer(cfg, trades="\n\n".join(blocks), trade_ids=[t["trade_id"] for t in trips],
                                      playbook=kb.render_playbook(), llm=llm)
    by_id = {p["trade_id"]: p for p in obj["postmortems"]}
    changes = []
    for t in trips:
        ops = [{**op, "origin": t["trade_id"]} for op in by_id[t["trade_id"]]["playbook_ops"]]
        changes += kb.apply_playbook_ops(ops, as_of, "post-mortem", cfg)
    items = [{"trade_id": t["trade_id"], "ticker": t["ticker"], "open_date": t["open_date"],
              "close_date": t["close_date"], "pnl": str(t["pnl"]), "pnl_pct": str(t["pnl_pct"]),
              "fees": str(t["fees"]), "benchmark_pct": t["benchmark_pct"], "written_on": as_of,
              **{k: by_id[t["trade_id"]][k] for k in ("expected", "happened", "predictable", "lesson")}}
             for t in trips]
    journal.add_postmortems(items)
    return items, changes
