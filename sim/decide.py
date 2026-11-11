"""Runs LLM d'une journée.

run_decision  (soir de séance) : screener → analystes → chef → validation automatique → risque → ordres en attente
run_veille    (week-end / férié) : analyste actualité seul, mise à jour des notes de contexte
run_postmortems : post-mortem de chaque position clôturée qui n'en a pas encore

Une panne LLM ne fait jamais échouer le run : elle est notée dans entry["errors"], et la sécurité prime
(chef indisponible → aucun ordre ; risque indisponible → tous les ordres bloqués).
"""
import copy
import json
from datetime import date, datetime, timedelta

from . import agents
from . import market_calendar as mc
from .broker import plan_orders
from .config import fee_grid
from .journal import Journal, digest
from .ledger import Ledger, apply_fill, round_trips, summary
from .llm import LLMError, claude_cli
from .models import Order, PortfolioState
from .money import D, cents
from .news import NewsItem, format_news
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
    return "\n".join(f"- {p['trade_id']} ({p['pnl_pct']} %) : {p['lesson']}" for p in pms) or "(aucune pour l'instant)"


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


# --- Run de décision ------------------------------------------------------------

def run_decision(*, as_of: str, run_at: datetime, cfg: dict, store, instruments: dict, ledger: Ledger,
                 journal: Journal, news_items: list[NewsItem], bench_summary: dict | None = None,
                 executions: list[dict] | None = None, llm=claude_cli) -> dict:
    grid = fee_grid(cfg)
    state = ledger.state()
    held = sorted(state.positions)
    marks = store.marks(held, as_of)
    rows = screen(store, instruments, as_of, cfg, held)
    table = select(rows, cfg, held, state, marks, grid)
    h = horizon(ledger, as_of, cfg)
    agents_summary = summary(state, marks)

    entry = {"date": as_of, "mode": "trading", "run_at": run_at.isoformat(), "exec_date": h["exec_date"],
             "horizon": h, "errors": [], "llm_calls": [], "executions": executions or [],
             "portfolio": _jsonable(agents_summary), "benchmark": _jsonable(bench_summary or {}),
             "screener": {"breadth": breadth(rows), "rows": table},
             "news": [i.__dict__ for i in news_items]}

    txt = {"horizon": horizon_text(h, as_of), "portfolio": portfolio_text(state, marks),
           "screener": format_table(table), "lessons": lessons_text(journal, cfg["agents"]["postmortems"])}

    market = _call(entry, "analyste marché", lambda: agents.market_analyst(
        cfg, horizon=txt["horizon"], portfolio=txt["portfolio"], breadth=_json_text(entry["screener"]["breadth"]),
        screener=txt["screener"], llm=llm))
    news = _call(entry, "analyste actualité", lambda: agents.news_analyst(
        cfg, horizon=txt["horizon"], portfolio=txt["portfolio"], notes=journal.context_notes(),
        news=format_news(news_items), llm=llm))
    entry["market_analysis"], entry["news_analysis"] = market, news
    if news:
        journal.set_context_notes(news["context_notes"])

    recent = journal.recent(as_of, cfg["agents"]["journal_days"])
    chef = _call(entry, "chef", lambda: agents.chef(
        cfg, horizon=txt["horizon"], portfolio=txt["portfolio"],
        performance=performance_text(agents_summary, bench_summary), market=_json_text(market),
        news=_json_text(news), screener=txt["screener"],
        journal="\n\n".join(digest(e) for e in recent) or "(premier jour)", lessons=txt["lessons"], llm=llm))
    entry["chef"] = chef

    final, refused = [], []
    if chef and chef["orders"]:
        final, refused = _validate_and_review(entry, chef, state, instruments, store, marks, cfg, grid,
                                              as_of, txt, market, news, ledger, llm)
    entry["final_orders"], entry["refused"] = final, refused
    journal.save_pending(as_of, h["exec_date"], final)
    journal.write_day(entry)
    return entry


def _validate_and_review(entry, chef, state, instruments, store, marks, cfg, grid, as_of, txt,
                         market, news, ledger, llm):
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
        lines.append(f"[{i}] {f.side} {f.qty} {f.ticker}{note} ≈ {f.amount} € + frais {f.fees} € ; "
                     f"poids après ≈ {weight:.1f} % ; thèse : {o['thesis']} ; sortie : {o['exit_plan']}")
    refused_txt = "\n".join(f"- {r['side']} {r['ticker']} : {r['reason']}" for r in refused) or "(aucun)"

    risk = _call(entry, "gestionnaire de risque", lambda: agents.risk_manager(
        cfg, n_orders=len(accepted), horizon=txt["horizon"], portfolio=txt["portfolio"], orders="\n".join(lines),
        refused=refused_txt, market=_json_text(market), news=_json_text(news), lessons=txt["lessons"], llm=llm))
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
                          "est_fees": str(f.fees), "thesis": o["thesis"], "sources": o["sources"],
                          "exit_plan": o["exit_plan"], "validation_note": out.verdict.reason,
                          "risk_note": review["reason"]})
    return final, refused


# --- Veille -------------------------------------------------------------------------

def run_veille(*, today: str, run_at: datetime, cfg: dict, ledger: Ledger, store, journal: Journal,
               news_items: list[NewsItem], llm=claude_cli) -> dict:
    state = ledger.state()
    last = mc.previous_session(today) if not mc.is_trading_day(today) else today
    marks = store.marks(list(state.positions), last)
    entry = {"date": today, "mode": "veille", "run_at": run_at.isoformat(), "errors": [], "llm_calls": [],
             "news": [i.__dict__ for i in news_items]}
    news = _call(entry, "analyste actualité", lambda: agents.news_analyst(
        cfg, horizon=f"Jour sans cotation ({today}) : veille uniquement, aucun ordre.",
        portfolio=portfolio_text(state, marks), notes=journal.context_notes(), news=format_news(news_items), llm=llm))
    entry["news_analysis"] = news
    if news:
        journal.set_context_notes(news["context_notes"])
    journal.write_day(entry)
    return entry


# --- Post-mortems -------------------------------------------------------------------

def run_postmortems(*, as_of: str, cfg: dict, ledger: Ledger, store, journal: Journal, llm=claude_cli) -> list[dict]:
    done = {p["trade_id"] for p in journal.postmortems()}
    trips = [t for t in round_trips(ledger.fills()) if t["closed"] and t["trade_id"] not in done]
    if not trips:
        return []

    bench = cfg["benchmark"]["ticker"]
    blocks = []
    for t in trips:
        b0, b1 = store.price(bench, t["open_date"]), store.price(bench, t["close_date"])
        t["benchmark_pct"] = str(round((b1 / b0 - 1) * 100, 2)) if b0 and b1 else None
        theses = [f"{e['date']} {o['side']} : {o['thesis']} (sortie prévue : {o['exit_plan']})"
                  for e in journal.recent(mc.next_session(t["close_date"]), 60)
                  if mc.previous_session(t["open_date"]) <= e["date"] < t["close_date"]
                  for o in e.get("final_orders", []) if o["ticker"] == t["ticker"]]
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
                                      llm=llm)
    by_id = {p["trade_id"]: p for p in obj["postmortems"]}
    items = [{"trade_id": t["trade_id"], "ticker": t["ticker"], "open_date": t["open_date"],
              "close_date": t["close_date"], "pnl": str(t["pnl"]), "pnl_pct": str(t["pnl_pct"]),
              "fees": str(t["fees"]), "benchmark_pct": t["benchmark_pct"], "written_on": as_of,
              **{k: by_id[t["trade_id"]][k] for k in ("expected", "happened", "lesson")}} for t in trips]
    journal.add_postmortems(items)
    return items
