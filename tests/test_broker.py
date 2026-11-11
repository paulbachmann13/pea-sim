import random

from sim.broker import buy_max, execute_orders, execution_price, plan_orders
from sim.ledger import summary
from sim.models import BUY, SELL, Order, PortfolioState, Position
from sim.money import D
from sim.validator import buy_cost


def test_execution_price_spread():
    assert execution_price(100, BUY, "0.20") == D("100.1000")
    assert execution_price(100, SELL, "0.20") == D("99.9000")


def test_sells_execute_before_buys(cfg, grid, instruments):
    st = PortfolioState(cash=D(100), deposits=D(1100), positions={"AIR.PA": Position(50, D(1000))})
    orders = [Order("TTE.PA", BUY, 30), Order("AIR.PA", SELL, 50)]
    out = plan_orders(orders, st, instruments, {"TTE.PA": 10, "AIR.PA": 20}, {}, cfg, grid,
                      "2026-11-12", allow_reduce=False)
    assert [o.order.side for o in out] == [SELL, BUY]
    assert all(o.verdict.ok for o in out)
    assert st.cash == D(100)                     # l'état d'origine n'est pas modifié


def test_max_orders_per_day(cfg, grid, instruments):
    st = PortfolioState(cash=D(1000), deposits=D(1000))
    orders = [Order("TTE.PA", BUY, 1)] * 6
    out = plan_orders(orders, st, instruments, {"TTE.PA": 10}, {}, cfg, grid, "d", allow_reduce=False)
    assert sum(o.verdict.ok for o in out) == 5
    assert "limite de 5 ordres" in out[-1].verdict.reason


def test_missing_price_refused(cfg, grid, instruments):
    st = PortfolioState(cash=D(1000), deposits=D(1000))
    out = plan_orders([Order("TTE.PA", BUY, 1)], st, instruments, {}, {}, cfg, grid, "d", allow_reduce=False)
    assert not out[0].verdict.ok and "cours" in out[0].verdict.reason


def test_execute_records_fills_and_rejections(cfg, grid, instruments, ledger):
    ledger.deposit("2026-11-11", 1000)
    orders = [Order("TTE.PA", BUY, 10), Order("AAPL.DE", BUY, 1)]
    execute_orders(ledger, orders, {"TTE.PA": 30, "AAPL.DE": 200}, {}, instruments, cfg, grid, "2026-11-12")
    fills = ledger.fills()
    assert len(fills) == 1 and fills[0].price == D("30.0300") and fills[0].fees == D("1.50")
    assert ledger.state().cash == D("1000") - D("300.30") - D("1.50")
    reasons = ledger.db.execute("SELECT ticker, reason FROM rejections").fetchall()
    assert reasons == [("AAPL.DE", "instrument inconnu du référentiel")]


def test_buy_max_benchmark(cfg, grid, ledger):
    ledger.deposit("2026-11-11", 1000)
    f = buy_max(ledger, "WPEA.PA", "5.50", cfg, grid, "2026-11-12")
    assert f is not None and ledger.state().cash >= 0
    assert buy_cost(f.qty + 1, f.price, grid) > 1000


def test_random_trading_keeps_invariants(cfg, grid, instruments, ledger):
    """30 jours d'ordres aléatoires : cash jamais négatif, concentration respectée, compta équilibrée."""
    rng = random.Random(42)
    ledger.deposit("2026-11-02", 1000)
    prices = {t: D(rng.uniform(3, 150)).quantize(D("0.01")) for t in instruments}
    tickers = list(instruments)
    for day in range(30):
        date = f"2026-12-{day + 1:02d}"
        prices = {t: max(D("0.50"), (p * D(rng.uniform(0.93, 1.07))).quantize(D("0.01"))) for t, p in prices.items()}
        held = ledger.state().positions
        orders = []
        for _ in range(rng.randint(0, 7)):
            t = rng.choice(tickers)
            if held.get(t) and rng.random() < 0.5:
                orders.append(Order(t, SELL, rng.randint(1, held[t].qty + 2)))
            else:
                orders.append(Order(t, BUY, rng.randint(1, 60)))
        before = ledger.state()
        execute_orders(ledger, orders, prices, prices, instruments, cfg, grid, date)
        s = ledger.state()
        assert s.cash >= 0
        for t, pos in s.positions.items():
            if pos.qty > before.qty(t):                  # titre renforcé aujourd'hui
                assert pos.qty * prices[t] <= D("0.4") * s.total_value(prices) + 1
        if rng.random() < 0.1 and s.positions:
            ledger.record_dividend(date, rng.choice(list(s.positions)), "0.30")
        out = summary(ledger.state(), prices)
        assert out["total"] == out["deposits"] + out["realized_pnl"] + out["unrealized_pnl"] + out["dividends"]
    assert ledger.fills(), "le scénario aléatoire doit produire des exécutions"
