"""Rapport de fin de simulation : chiffres (compta déterministe) + analyse rédigée par un LLM.

Usage manuel (rapport provisoire, sans LLM) : python -m sim.report [--llm]
"""
import json
import sys
from decimal import Decimal

from . import agents
from . import market_calendar as mc
from .journal import Journal, digest, write_json
from .ledger import Ledger, round_trips, summary
from .llm import LLMError, claude_cli
from .money import cents

LABELS = {"agents": "Équipe d'agents", "agents_lowcost": "Agents, grille low-cost",
          "benchmark": "ETF Monde (buy & hold)", "random": "Aléatoire (contrôle)"}


def trades_with_marks(led: Ledger, store, as_of: str) -> list[dict]:
    """Allers-retours ; les positions encore ouvertes sont valorisées à la clôture de `as_of`."""
    trips = round_trips(led.fills())
    for t in trips:
        if not t["closed"]:
            px = store.last_close(t["ticker"], as_of)
            value = px * t["held"] if px is not None else Decimal("0")
            t["pnl"] = t["sold"] + value - t["bought"] - t["fees"]
            t["pnl_pct"] = (t["pnl"] / t["bought"] * 100).quantize(Decimal("0.01"))
            t["close_date"] = f"ouverte ({as_of})"
    return trips


def compute_stats(ledgers: dict[str, Ledger], store, journal: Journal, as_of: str) -> dict:
    books = {}
    for b, led in ledgers.items():
        st = led.state()
        s = summary(st, store.marks(list(st.positions), as_of))
        books[b] = {"label": LABELS[b], "total": str(cents(s["total"])), "return_pct": f"{s['return_pct']:.2f}",
                    "fees": str(s["fees_paid"]), "dividends": str(s["dividends"]), "n_fills": len(led.fills()),
                    "final_positions": {t: p.qty for t, p in st.positions.items()}}
    trips = trades_with_marks(ledgers["agents"], store, as_of)
    ranked = sorted(trips, key=lambda t: t["pnl"], reverse=True)
    keep = ("trade_id", "ticker", "open_date", "close_date", "qty_bought", "avg_buy", "avg_sell", "fees", "pnl", "pnl_pct")

    def slim(t):
        return {k: (str(t[k]) if isinstance(t[k], Decimal) else t[k]) for k in keep}

    rejections = ledgers["agents"].rejections()
    days = [journal.read_day(d) for d in journal.trading_days()]
    return {
        "as_of": as_of, "start": ledgers["agents"].start_date(), "books": books,
        "best_trades": [slim(t) for t in ranked[:3]], "worst_trades": [slim(t) for t in ranked[::-1][:3]],
        "all_trades": [slim(t) for t in trips],
        "orders_decided": sum(len(e.get("final_orders", [])) for e in days),
        "refused_by_validation": sum(1 for r in rejections if r["stage"] in ("decision", "execution")),
        "blocked_by_risk": sum(1 for r in rejections if r["stage"] == "risk"),
        "llm_errors": sum(len(e.get("errors", [])) for e in days),
        "sessions": len(mc.sessions_between(ledgers["agents"].start_date(), as_of)),
    }


def to_markdown(stats: dict, analysis: dict | None) -> str:
    lines = [f"# Rapport final — simulation PEA du {stats['start']} au {stats['as_of']}", "",
             "| Livre | Valeur finale | Performance | Frais | Dividendes | Exécutions |", "|---|---|---|---|---|---|"]
    for b in stats["books"].values():
        lines.append(f"| {b['label']} | {b['total']} € | {b['return_pct']} % | {b['fees']} € | {b['dividends']} € | {b['n_fills']} |")
    lines += ["", f"Ordres décidés : {stats['orders_decided']} — refusés par les contrôles : "
              f"{stats['refused_by_validation']} — bloqués par le risque : {stats['blocked_by_risk']} — "
              f"erreurs LLM : {stats['llm_errors']}", ""]
    for title, key in (("Meilleurs trades", "best_trades"), ("Pires trades", "worst_trades")):
        lines.append(f"## {title}")
        lines += [f"- {t['ticker']} ({t['open_date']} → {t['close_date']}) : {t['pnl']} € ({t['pnl_pct']} %)"
                  for t in stats[key]] or ["- (aucun)"]
        lines.append("")
    if analysis:
        lines += ["## Verdict", analysis["verdict"], "", "## Ce qui a fonctionné"]
        lines += [f"- {x}" for x in analysis["what_worked"]]
        lines += ["", "## Ce qui n'a pas fonctionné"] + [f"- {x}" for x in analysis["what_failed"]]
        lines += ["", "## Frais", analysis["fees_impact"], "", "## Fonctionnement de l'équipe", analysis["process"],
                  "", "## Leçons"] + [f"- {x}" for x in analysis["lessons"]]
    return "\n".join(lines) + "\n"


def build_report(cfg: dict, ledgers: dict[str, Ledger], store, journal: Journal, as_of: str,
                 llm=claude_cli, with_llm: bool = True, final: bool = True) -> dict:
    stats = compute_stats(ledgers, store, journal, as_of)
    analysis, error = None, None
    if with_llm:
        try:
            analysis, _ = agents.report_writer(
                cfg, stats=json.dumps(stats["books"], ensure_ascii=False, indent=1),
                trades=json.dumps(stats["all_trades"], ensure_ascii=False, indent=1),
                postmortems=json.dumps(journal.postmortems(), ensure_ascii=False, indent=1),
                journal="\n\n".join(digest(journal.read_day(d)) for d in journal.trading_days()), llm=llm)
        except LLMError as e:
            error = str(e)
    report = {"stats": stats, "analysis": analysis, "analysis_error": error}
    name = "rapport-final" if final else "rapport-provisoire"
    write_json(journal.root / f"{name}.json", report)
    (journal.root / f"{name}.md").write_text(to_markdown(stats, analysis), encoding="utf-8")
    if error:
        raise LLMError(f"analyse du rapport indisponible : {error}")
    return report


def main():
    from .config import ROOT, load_config
    from .daily import open_env
    from .data import PriceStore

    cfg = load_config()
    env = open_env(cfg, ROOT)
    store = PriceStore(ROOT / cfg["data"]["market_db"])
    as_of = env.agents.last_snapshot_date()
    if not as_of:
        sys.exit("La simulation n'a pas encore démarré.")
    build_report(cfg, env.ledgers, store, env.journal, as_of, with_llm="--llm" in sys.argv, final=False)
    print((env.journal.root / "rapport-provisoire.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
