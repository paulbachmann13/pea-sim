"""Calendrier des séances Euronext (jours fériés de bourse inclus).

On utilise le calendrier de Paris (XPAR) : Euronext harmonise ses jours fériés entre places.
Un titre sans cotation un jour donné (ex. férié propre à Milan) n'est simplement pas négociable ce jour-là.
"""
from datetime import date

import exchange_calendars as xcals
import pandas as pd

_CAL = None


def _cal():
    global _CAL
    if _CAL is None:
        _CAL = xcals.get_calendar("XPAR")
    return _CAL


def _ts(d) -> pd.Timestamp:
    return pd.Timestamp(d).normalize()


def is_trading_day(d: date | str) -> bool:
    return bool(_cal().is_session(_ts(d)))


def previous_session(d: date | str) -> str:
    """Dernière séance strictement avant `d`."""
    return _cal().date_to_session(_ts(d) - pd.Timedelta(days=1), direction="previous").strftime("%Y-%m-%d")


def next_session(d: date | str) -> str:
    """Première séance strictement après `d`."""
    return _cal().date_to_session(_ts(d) + pd.Timedelta(days=1), direction="next").strftime("%Y-%m-%d")


def sessions_between(start: date | str, end: date | str) -> list[str]:
    """Séances de `start` à `end` inclus."""
    return [s.strftime("%Y-%m-%d") for s in _cal().sessions_in_range(_ts(start), _ts(end))]
