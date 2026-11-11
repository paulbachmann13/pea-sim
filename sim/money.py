"""Arithmétique monétaire : Decimal partout, arrondi au centime."""
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
PRICE_STEP = Decimal("0.0001")


def D(x) -> Decimal:
    """Convertit en Decimal en passant par str (évite les erreurs binaires des float)."""
    return x if isinstance(x, Decimal) else Decimal(str(x))


def cents(x) -> Decimal:
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def price4(x) -> Decimal:
    return D(x).quantize(PRICE_STEP, rounding=ROUND_HALF_UP)
