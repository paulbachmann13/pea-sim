"""Calcul des frais de courtage à partir d'une grille de config."""
from decimal import Decimal

from .money import D, cents


def compute_fees(amount, grid: dict) -> Decimal:
    """Frais pour un ordre de `amount` € (montant brut = quantité × prix)."""
    amount = D(amount)
    if amount <= 0:
        raise ValueError(f"montant d'ordre invalide : {amount}")

    tier = next(t for t in grid["tiers"] if t.get("up_to") is None or amount <= D(t["up_to"]))
    if "fixed" in tier:
        fee = D(tier["fixed"])
    else:
        fee = max(amount * D(tier["rate"]), D(tier.get("min", 0)))

    cap = grid.get("pea_cap_rate")
    if cap is not None:
        fee = min(fee, amount * D(cap))
    return cents(fee)
