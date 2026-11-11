"""Mise à jour des données de marché pour un run (appelée par le run quotidien).

Usage manuel : python -m sim.refresh [AAAA-MM-JJ]
"""
import logging
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from . import market_calendar as mc
from .config import ROOT, load_config
from .data import PriceStore, update_prices
from .universe import build_instruments, ensure_universe, etf_entries, prefilter, refs


def refresh_market_data(cfg: dict, as_of: str, held: set[str] = frozenset(), root: Path = ROOT):
    """Met à jour référentiel + prix jusqu'à `as_of` (une séance). Renvoie (store, universe, instruments, issues)."""
    d = cfg["data"]
    (root / "data").mkdir(exist_ok=True)
    store = PriceStore(root / d["market_db"])
    universe = {**ensure_universe(root / d["universe_path"], d["universe_max_age_days"]), **etf_entries(cfg)}
    tickers = sorted(set(prefilter(universe, cfg)) | set(etf_entries(cfg)) | set(held))
    start = (date.fromisoformat(as_of) - timedelta(days=d["history_days"])).isoformat()
    cross = set(held) | {cfg["benchmark"]["ticker"]}
    issues = update_prices(store, tickers, refs(universe), start, as_of, [as_of], cfg,
                           cross_check=cross, run_date=as_of)
    instruments = build_instruments(universe, store, as_of, cfg, tickers)
    return store, universe, instruments, issues


def main():
    logging.basicConfig(level=logging.WARNING)
    cfg = load_config()
    as_of = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    if not mc.is_trading_day(as_of):
        as_of = mc.previous_session(as_of)
    store, universe, instruments, issues = refresh_market_data(cfg, as_of)

    liquid = [t for t, i in instruments.items()
              if i.kind == "equity" and i.avg_daily_volume_eur
              and i.avg_daily_volume_eur >= cfg["liquidity"]["min_avg_daily_volume_eur"]]
    print(f"Séance {as_of} | référentiel {len(universe)} titres | téléchargés {len(instruments)} "
          f"| liquides (>= seuil) {len(liquid)}")
    print("Anomalies :", dict(Counter((i.kind, i.blocking) for i in issues)))
    for i in issues:
        if i.kind in ("cross_check", "source_error") or (i.blocking and i.ticker in etf_entries(cfg)):
            print("  ", i)


if __name__ == "__main__":
    main()
