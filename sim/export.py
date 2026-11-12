"""Export des données du dashboard (docs/data/*.json), lues par la PWA statique."""
import shutil
from pathlib import Path

from . import market_calendar as mc
from .decide import horizon
from .journal import Journal, read_json, write_json
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
                      "view": ((e.get("chef") or {}).get("market_view") or
                               (e.get("news_analysis") or {}).get("summary") or "")[:160]})
    write_json(out / "journal" / "index.json", sorted(index, key=lambda x: x["file"], reverse=True))
    write_json(out / "postmortems.json", journal.postmortems())
    report = journal.root / "rapport-final.json"
    if report.exists():
        shutil.copyfile(report, out / "report.json")
    return out
