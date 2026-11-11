"""Validation d'un ordre : règles PEA, liquidité, cash, concentration.

Chaque fonction de contrôle renvoie None si c'est bon, sinon le motif du refus.
"""
import re
from dataclasses import dataclass
from decimal import Decimal

from .fees import compute_fees
from .models import BUY, SELL, Instrument, Order, PortfolioState
from .money import D, cents

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    qty: int = 0          # quantité retenue (peut être réduite)
    reason: str = ""      # motif du refus, ou note en cas de réduction


def refuse(reason: str) -> Verdict:
    return Verdict(False, 0, reason)


# --- Contrôles de référentiel ------------------------------------------------

def check_pea_eligibility(inst: Instrument | None, cfg: dict) -> str | None:
    if inst is None:
        return "instrument inconnu du référentiel"
    el = cfg["eligibility"]
    if inst.kind == "etf":
        if inst.ticker not in el["etf_whitelist"]:
            return f"ETF {inst.ticker} absent de la liste blanche PEA"
        return None
    suffix = inst.ticker.rsplit(".", 1)[1] if "." in inst.ticker else ""
    if suffix not in el["exchange_suffixes"]:
        return f"place de cotation non autorisée ({suffix or 'aucun suffixe'})"
    if inst.currency not in el["currencies"]:
        return f"devise non autorisée ({inst.currency})"
    if not inst.isin or not ISIN_RE.match(inst.isin):
        return f"ISIN manquant ou invalide ({inst.isin})"
    if inst.isin[:2] not in el["isin_countries"]:
        return f"siège hors UE/EEE (ISIN {inst.isin})"
    return None


def check_liquidity(inst: Instrument, cfg: dict) -> str | None:
    liq = cfg["liquidity"]
    if inst.kind == "etf" and not liq["applies_to_etf"]:
        return None
    if inst.avg_daily_volume_eur is None:
        return "volume moyen inconnu"
    if D(inst.avg_daily_volume_eur) < D(liq["min_avg_daily_volume_eur"]):
        return (f"liquidité insuffisante : {D(inst.avg_daily_volume_eur):,.0f} €/j "
                f"< seuil {D(liq['min_avg_daily_volume_eur']):,.0f} €/j")
    return None


# --- Quantités maximales -----------------------------------------------------

def _largest(pred, hi: int) -> int:
    """Plus grand q dans [0, hi] tel que pred(q), pour pred décroissant (vrai puis faux)."""
    lo = 0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if pred(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


def buy_cost(qty: int, price, grid: dict) -> Decimal:
    amount = cents(qty * D(price))
    return amount + compute_fees(amount, grid)


def max_buy_qty(cash, price, grid: dict) -> int:
    """Nombre max d'actions entières achetables avec `cash`, frais inclus."""
    cash, price = D(cash), D(price)
    if price <= 0 or cash <= 0:
        return 0
    return _largest(lambda q: buy_cost(q, price, grid) <= cash, int(cash // price))


def max_qty_concentration(state: PortfolioState, ticker: str, price, marks: dict, cfg: dict, grid: dict) -> int:
    """Nombre max d'actions achetables sans dépasser max_position_pct après l'achat."""
    limit = D(cfg["limits"]["max_position_pct"]) / 100
    price, mid = D(price), D(marks[ticker])
    held = state.qty(ticker)
    total = state.total_value(marks)

    def ok(q):
        amount = cents(q * price)
        total_after = total - (amount - q * mid) - compute_fees(amount, grid)
        return (held + q) * mid <= limit * total_after

    return _largest(ok, int(total // price) + 1)


# --- Validation d'un ordre ---------------------------------------------------

def validate_order(order: Order, state: PortfolioState, inst: Instrument | None, price,
                   marks: dict, cfg: dict, grid: dict, allow_reduce: bool = False) -> Verdict:
    """`price` = prix d'exécution (spread inclus) ; `marks` = cours de valorisation."""
    if order.side not in (BUY, SELL):
        return refuse(f"sens inconnu : {order.side}")
    if not isinstance(order.qty, int) or isinstance(order.qty, bool) or order.qty <= 0:
        return refuse(f"quantité invalide (entier > 0 requis) : {order.qty}")
    price = D(price)
    if price <= 0:
        return refuse(f"prix invalide : {price}")

    if order.side == SELL:
        held = state.qty(order.ticker)
        if order.qty > held:
            return refuse(f"vente à découvert interdite : détenu {held}, demandé {order.qty}")
        amount = cents(order.qty * price)
        if state.cash + amount - compute_fees(amount, grid) < 0:
            return refuse("frais de vente supérieurs au cash disponible")
        return Verdict(True, order.qty)

    reason = check_pea_eligibility(inst, cfg) or check_liquidity(inst, cfg)
    if reason:
        return refuse(reason)

    q_cash = max_buy_qty(state.cash, price, grid)
    q_conc = max_qty_concentration(state, order.ticker, price, marks, cfg, grid)
    q_max = min(q_cash, q_conc)
    if order.qty <= q_max:
        return Verdict(True, order.qty)

    why = []
    if q_cash < order.qty:
        why.append(f"cash insuffisant (max {q_cash})")
    if q_conc < order.qty:
        why.append(f"limite {cfg['limits']['max_position_pct']} % par titre (max {q_conc})")
    why = ", ".join(why)
    if allow_reduce and q_max > 0:
        return Verdict(True, q_max, f"quantité réduite de {order.qty} à {q_max} : {why}")
    return refuse(why)
