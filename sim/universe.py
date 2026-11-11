"""Référentiel des titres : liste officielle Euronext (nom, ISIN, place, devise).

Sert à valider l'éligibilité PEA (ISIN, place) et fournit l'ISIN + MIC pour la source de prix Euronext.
Le fichier est retéléchargé chaque semaine.
"""
import csv
import io
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .data import HTTP_HEADERS, PriceStore
from .models import Instrument
from .money import D

EURONEXT_LIST_URL = "https://live.euronext.com/en/pd_es/data/stocks/download?mics=dm_all_stock"

# Marché Euronext -> (suffixe yfinance, MIC). Les marchés absents (Global Equity Market, EuroTLX,
# Expert Market, Trading After Hours, Oslo…) sont ignorés.
MARKETS = {
    "Euronext Paris": ("PA", "XPAR"),
    "Euronext Growth Paris": ("PA", "ALXP"),
    "Euronext Access Paris": ("PA", "XMLI"),
    "Euronext Amsterdam": ("AS", "XAMS"),
    "Euronext Brussels": ("BR", "XBRU"),
    "Euronext Growth Brussels": ("BR", "ALXB"),
    "Euronext Access Brussels": ("BR", "MLXB"),
    "Euronext Lisbon": ("LS", "XLIS"),
    "Euronext Growth Lisbon": ("LS", "ALXL"),
    "Euronext Access Lisbon": ("LS", "ENXL"),
    "Euronext Dublin": ("IR", "XMSM"),
    "Euronext Growth Dublin": ("IR", "XESM"),
    "Euronext Milan": ("MI", "MTAA"),
    "Euronext Growth Milan": ("MI", "EXGM"),
}
SUFFIX_MIC = {"PA": "XPAR", "AS": "XAMS", "BR": "XBRU", "LS": "XLIS", "IR": "XMSM", "MI": "MTAA"}
FIELDS = ["ticker", "isin", "name", "market", "mic", "currency", "kind", "turnover"]


@dataclass(frozen=True)
class RefEntry:
    ticker: str
    isin: str
    name: str
    market: str
    mic: str
    currency: str
    kind: str = "equity"
    turnover: float = 0.0        # capitaux échangés lors de la dernière séance (€)


def _num(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_euronext_list(text: str) -> dict[str, RefEntry]:
    lines = text.lstrip("﻿").splitlines()
    rows = csv.DictReader(io.StringIO("\n".join(lines[:1] + lines[4:])), delimiter=";")
    out = {}
    for r in rows:
        market = r["Market"].split(",")[0].strip()      # cotations multiples : première place
        if market not in MARKETS or not r["Symbol"]:
            continue
        suffix, mic = MARKETS[market]
        ticker = f"{r['Symbol'].strip()}.{suffix}"
        if ticker in out:
            continue
        out[ticker] = RefEntry(ticker, r["ISIN"].strip(), r["Name"].strip(), market, mic,
                               r["Currency"].strip(), "equity", _num(r["Turnover"]))
    return out


def save_universe(path: Path, universe: dict[str, RefEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        for e in universe.values():
            w.writerow(e.__dict__)


def load_universe(path: Path) -> dict[str, RefEntry]:
    with open(path, encoding="utf-8") as f:
        return {r["ticker"]: RefEntry(**{**r, "turnover": float(r["turnover"])}) for r in csv.DictReader(f)}


def ensure_universe(path: Path, max_age_days: int) -> dict[str, RefEntry]:
    """Charge le référentiel, en le retéléchargeant s'il est absent ou trop vieux."""
    path = Path(path)
    if not path.exists() or time.time() - path.stat().st_mtime > max_age_days * 86400:
        r = requests.get(EURONEXT_LIST_URL, headers=HTTP_HEADERS, timeout=60)
        r.raise_for_status()
        universe = parse_euronext_list(r.content.decode("utf-8-sig"))
        if len(universe) < 500:
            raise RuntimeError(f"référentiel Euronext anormalement court ({len(universe)} titres)")
        save_universe(path, universe)
    return load_universe(path)


def etf_entries(cfg: dict) -> dict[str, RefEntry]:
    out = {}
    for ticker, isin in cfg["eligibility"]["etf_whitelist"].items():
        mic = SUFFIX_MIC[ticker.rsplit(".", 1)[1]]
        out[ticker] = RefEntry(ticker, isin, ticker, "ETF", mic, "EUR", "etf")
    return out


def prefilter(universe: dict[str, RefEntry], cfg: dict) -> list[str]:
    """Candidats au screening : actions PEA plausibles et assez échangées la veille."""
    el, liq = cfg["eligibility"], cfg["liquidity"]
    floor = liq["min_avg_daily_volume_eur"] * cfg["data"]["prefilter_turnover_ratio"]
    return sorted(
        t for t, e in universe.items()
        if e.kind == "equity" and e.currency in el["currencies"]
        and e.isin[:2] in el["isin_countries"] and e.turnover >= floor
    )


def refs(universe: dict[str, RefEntry]) -> dict[str, tuple[str, str]]:
    return {t: (e.isin, e.mic) for t, e in universe.items()}


def build_instruments(universe: dict[str, RefEntry], store: PriceStore, as_of: str, cfg: dict,
                      tickers=None) -> dict[str, Instrument]:
    """Instruments pour la validation, avec volume moyen calculé sur les données connues à `as_of`."""
    window = cfg["liquidity"]["window_days"]
    out = {}
    for t in tickers if tickers is not None else universe:
        e = universe.get(t)
        if e is None:
            continue
        adv = store.avg_daily_volume_eur(t, as_of, window)
        out[t] = Instrument(t, e.isin, e.currency, e.kind, None if adv is None else D(adv))
    return out
