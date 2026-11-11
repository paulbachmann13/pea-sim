"""Courtier simulé : applique le spread, valide et exécute un lot d'ordres.

Le même `plan_orders` sert deux fois :
  - le soir, à la décision, avec les cours de clôture (contrôle sans réduction) ;
  - le lendemain, à l'exécution, avec les cours d'ouverture réels.
"""
import copy
from dataclasses import dataclass
from decimal import Decimal

from .fees import compute_fees
from .ledger import Ledger, apply_fill
from .models import BUY, SELL, Fill, Instrument, Order, PortfolioState
from .money import D, cents, price4
from .validator import Verdict, max_buy_qty, refuse, validate_order


@dataclass(frozen=True)
class Outcome:
    order: Order
    verdict: Verdict
    fill: Fill | None = None


def execution_price(ref_price, side: str, spread_pct) -> Decimal:
    half = D(spread_pct) / 100 / 2
    return price4(D(ref_price) * (1 + half if side == BUY else 1 - half))


def make_fill(date: str, ticker: str, side: str, qty: int, ref_price, cfg: dict, grid: dict) -> Fill:
    price = execution_price(ref_price, side, cfg["execution"]["spread_pct"])
    amount = cents(qty * price)
    return Fill(date, ticker, side, qty, price, D(ref_price), compute_fees(amount, grid), amount)


def plan_orders(orders: list[Order], state: PortfolioState, instruments: dict[str, Instrument],
                ref_prices: dict, marks: dict, cfg: dict, grid: dict, date: str,
                allow_reduce: bool) -> list[Outcome]:
    """Simule le lot sur une copie de l'état. Ventes d'abord (elles libèrent du cash), puis achats."""
    state = copy.deepcopy(state)
    marks = {**marks, **ref_prices}
    max_n = cfg["limits"]["max_orders_per_day"]
    kept, excess = orders[:max_n], orders[max_n:]
    kept = sorted(kept, key=lambda o: 0 if o.side == SELL else 1)

    outcomes = []
    for order in kept:
        ref = ref_prices.get(order.ticker)
        if ref is None:
            outcomes.append(Outcome(order, refuse("pas de cours disponible")))
            continue
        price = execution_price(ref, order.side, cfg["execution"]["spread_pct"])
        verdict = validate_order(order, state, instruments.get(order.ticker), price, marks, cfg, grid, allow_reduce)
        fill = None
        if verdict.ok:
            fill = make_fill(date, order.ticker, order.side, verdict.qty, ref, cfg, grid)
            apply_fill(state, fill)
        outcomes.append(Outcome(order, verdict, fill))

    outcomes += [Outcome(o, refuse(f"limite de {max_n} ordres/jour atteinte")) for o in excess]
    return outcomes


def execute_orders(ledger: Ledger, orders: list[Order], opens: dict, marks: dict,
                   instruments: dict[str, Instrument], cfg: dict, grid: dict, date: str) -> list[Outcome]:
    """Exécute au cours d'ouverture et enregistre exécutions et refus dans le grand livre."""
    outcomes = plan_orders(orders, ledger.state(), instruments, opens, marks, cfg, grid, date,
                           allow_reduce=cfg["execution"]["reduce_qty_if_needed"])
    for o in outcomes:
        if o.fill:
            ledger.record_fill(o.fill)
        else:
            ledger.record_rejection(date, "execution", o.order, o.verdict.reason)
    return outcomes


def buy_max(ledger: Ledger, ticker: str, ref_price, cfg: dict, grid: dict, date: str) -> Fill | None:
    """Achète le maximum d'actions entières possible (benchmark buy & hold, sans limite de concentration)."""
    price = execution_price(ref_price, BUY, cfg["execution"]["spread_pct"])
    qty = max_buy_qty(ledger.state().cash, price, grid)
    if qty == 0:
        return None
    fill = make_fill(date, ticker, BUY, qty, ref_price, cfg, grid)
    ledger.record_fill(fill)
    return fill
