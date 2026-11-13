"""Taux de change dollar → euro (référence BCE via frankfurter.dev), pour afficher la consommation en euros.

Le budget reste calculé en « équivalent $ API » (unité du CLI Claude) ; seul l'affichage est converti.
Un taux par jour est mis en cache (data/fx.json) ; sans réseau, on garde le dernier taux connu, puis le taux de secours.
"""
import logging
from datetime import date
from pathlib import Path

import requests

from .journal import read_json, write_json

log = logging.getLogger(__name__)
URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR"
FALLBACK = 0.92


def fetch_rate() -> tuple[float, str]:
    r = requests.get(URL, timeout=15)
    r.raise_for_status()
    j = r.json()
    return float(j["rates"]["EUR"]), j["date"]


def usd_to_eur(root: Path, today: date | None = None, fetch=fetch_rate) -> dict:
    """{"rate", "date", "source"} : taux du jour (cache), sinon dernier taux connu, sinon taux de secours."""
    cache = root / "data" / "fx.json"
    cached = read_json(cache, None)
    day = (today or date.today()).isoformat()
    if cached and cached.get("fetched") == day:
        return cached
    try:
        rate, rate_date = fetch()
        out = {"rate": rate, "date": rate_date, "source": "BCE", "fetched": day}
        write_json(cache, out)
        return out
    except Exception:
        log.warning("taux de change indisponible : dernier taux connu conservé")
        if cached:
            return {**cached, "source": "dernier taux connu"}
        return {"rate": FALLBACK, "date": None, "source": "taux approximatif", "fetched": None}
