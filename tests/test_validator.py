import pytest

from conftest import equity
from sim.models import BUY, SELL, Instrument, Order, PortfolioState, Position
from sim.money import D
from sim.validator import (check_liquidity, check_pea_eligibility, max_buy_qty,
                           max_qty_concentration, validate_order)


def state(cash=1000, **positions):
    return PortfolioState(cash=D(cash), deposits=D(cash),
                          positions={t.replace("_", "."): Position(q, D(c)) for t, (q, c) in positions.items()})


# --- Éligibilité et liquidité -------------------------------------------------

@pytest.mark.parametrize("inst, expected", [
    (equity(), None),
    (equity("ASML.AS", "NL0010273215"), None),
    (equity("AAPL.PA", "US0378331005"), "siège hors UE/EEE"),
    (equity("SAP.DE", "DE0007164600"), "place de cotation"),
    (equity("BP.L", "GB0007980591", "GBP"), "place de cotation"),
    (equity("TTE"), "place de cotation"),
    (equity("NOVO.CO", "DK0062498333", "DKK"), "place de cotation"),
    (equity("XX.PA", "FR0000120271", "USD"), "devise"),
    (equity("XX.PA", None), "ISIN manquant"),
    (equity("XX.PA", "FR12"), "ISIN manquant"),
    (Instrument("WPEA.PA", "IE0002XZSHO1", "EUR", "etf"), None),
    (Instrument("IWDA.AS", "IE00B4L5Y983", "EUR", "etf"), "liste blanche"),
    (None, "inconnu"),
])
def test_pea_eligibility(cfg, inst, expected):
    reason = check_pea_eligibility(inst, cfg)
    assert reason is None if expected is None else expected in reason


def test_liquidity(cfg):
    assert check_liquidity(equity(volume=2_000_000), cfg) is None
    assert "insuffisante" in check_liquidity(equity(volume=200_000), cfg)
    assert "inconnu" in check_liquidity(equity(volume=None), cfg)
    assert check_liquidity(Instrument("WPEA.PA", None, "EUR", "etf"), cfg) is None


# --- Quantités max ------------------------------------------------------------

@pytest.mark.parametrize("cash, price, expected", [
    ("1002.50", 10, 100),   # pile 1000 + 2,50
    ("1002.49", 10, 99),
    ("1006.00", 1, 1000),   # 1001 actions → palier 2 : 1001 + 5,01 > 1006
    ("1006.01", 1, 1001),
    ("5", 10, 0),
    ("0", 10, 0),
])
def test_max_buy_qty(grid, cash, price, expected):
    assert max_buy_qty(D(cash), D(price), grid) == expected


def test_max_qty_concentration(cfg, grid):
    # 40 × 10 = 400 > 40 % × (1000 − 2,00) ; 39 × 10 = 390 ≤ 40 % × (1000 − 1,95)
    assert max_qty_concentration(state(), "TTE.PA", 10, {"TTE.PA": 10}, cfg, grid) == 39


# --- validate_order -----------------------------------------------------------

def v(order, st, cfg, grid, price=10, marks=None, inst=None, allow_reduce=False):
    inst = inst or equity(order.ticker)
    return validate_order(order, st, inst, D(price), marks or {order.ticker: price}, cfg, grid, allow_reduce)


@pytest.mark.parametrize("qty", [0, -1, 1.5, True, "3"])
def test_invalid_quantity(cfg, grid, qty):
    verdict = v(Order("TTE.PA", BUY, qty), state(), cfg, grid)
    assert not verdict.ok and "quantité invalide" in verdict.reason


def test_unknown_side(cfg, grid):
    assert not v(Order("TTE.PA", "SHORT", 1), state(), cfg, grid).ok


def test_buy_ok(cfg, grid):
    verdict = v(Order("TTE.PA", BUY, 30), state(), cfg, grid)
    assert verdict.ok and verdict.qty == 30


def test_buy_refused_if_not_eligible(cfg, grid):
    verdict = v(Order("AAPL.PA", BUY, 1), state(), cfg, grid, inst=equity("AAPL.PA", "US0378331005"))
    assert not verdict.ok and "UE/EEE" in verdict.reason


def test_cash_insufficient_refused_or_reduced(cfg, grid):
    cfg["limits"]["max_position_pct"] = 100
    order = Order("TTE.PA", BUY, 20)
    refused = v(order, state(), cfg, grid, price=60)
    assert not refused.ok and "cash insuffisant" in refused.reason
    reduced = v(order, state(), cfg, grid, price=60, allow_reduce=True)
    assert reduced.ok and reduced.qty == 16 and "réduite" in reduced.reason


def test_concentration_refused_or_reduced(cfg, grid):
    order = Order("TTE.PA", BUY, 50)
    refused = v(order, state(), cfg, grid)
    assert not refused.ok and "40 %" in refused.reason
    reduced = v(order, state(), cfg, grid, allow_reduce=True)
    assert reduced.ok and reduced.qty == 39


def test_no_buy_when_position_already_above_limit(cfg, grid):
    st = state(500, TTE_PA=(50, 500))          # 50 × 20 = 1000 / 1500 = 67 %
    verdict = v(Order("TTE.PA", BUY, 1), st, cfg, grid, price=20, allow_reduce=True)
    assert not verdict.ok


def test_concentration_counts_existing_position(cfg, grid):
    # total 900 : 35 × 10 = 350 ≤ 40 % × (900 − 0,25) ; 36 × 10 = 360 > 40 % × (900 − 0,30)
    st = state(600, TTE_PA=(30, 300))
    assert max_qty_concentration(st, "TTE.PA", 10, {"TTE.PA": 10}, cfg, grid) == 5


def test_sell_rules(cfg, grid):
    st = state(0, TTE_PA=(10, 100))
    assert v(Order("TTE.PA", SELL, 10), st, cfg, grid).ok
    assert "découvert" in v(Order("TTE.PA", SELL, 11), st, cfg, grid).reason
    assert "découvert" in v(Order("AIR.PA", SELL, 1), st, cfg, grid).reason


def test_sell_does_not_require_eligibility(cfg, grid):
    st = state(0, XX_PA=(10, 100))
    inst = equity("XX.PA", volume=0)            # devenu illiquide : on doit pouvoir sortir
    assert v(Order("XX.PA", SELL, 10), st, cfg, grid, inst=inst).ok
