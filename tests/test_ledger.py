import pytest

from sim.ledger import Ledger, LedgerError, summary
from sim.models import BUY, SELL, Fill, Order
from sim.money import D


def fill(side, qty, price, fees, ticker="TTE.PA", date="2026-11-12"):
    price = D(price)
    return Fill(date, ticker, side, qty, price, price, D(fees), D(qty) * price)


@pytest.fixture
def book(ledger):
    ledger.deposit("2026-11-11", 1000)
    return ledger


def test_buy_sell_dividend_scenario(book):
    book.record_fill(fill(BUY, 10, 50, "2.50"))
    s = book.state()
    assert s.cash == D("497.50")
    assert s.positions["TTE.PA"].pru == D("50.25")

    book.record_fill(fill(SELL, 5, 60, "1.50"))
    s = book.state()
    assert s.cash == D("796.00")
    assert s.realized_pnl == D("47.25")         # 298,50 net − 5 × 50,25
    assert s.positions["TTE.PA"].cost_basis == D("251.25")

    assert book.record_dividend("2026-11-13", "TTE.PA", 1) == D("5.00")

    out = summary(book.state(), {"TTE.PA": 55})
    assert out["cash"] == D("801.00")
    assert out["total"] == D("1076.00")
    assert out["unrealized_pnl"] == D("23.75")
    assert out["fees_paid"] == D("4.00")
    assert out["return_pct"] == D("7.6")


def test_full_sell_closes_position(book):
    book.record_fill(fill(BUY, 3, "33.33", "1.00"))
    book.record_fill(fill(SELL, 3, "40", "1.00"))
    s = book.state()
    assert "TTE.PA" not in s.positions
    assert s.realized_pnl == D("120") - D("1") - (D("99.99") + D("1"))


def test_overdraft_is_rejected_and_not_recorded(book):
    with pytest.raises(LedgerError):
        book.record_fill(fill(BUY, 100, 10, "2.50"))   # 1002,50 > 1000
    assert book.state().cash == D("1000")
    assert book.fills() == []


def test_short_sell_is_rejected(book):
    book.record_fill(fill(BUY, 2, 10, "0.10"))
    with pytest.raises(LedgerError):
        book.record_fill(fill(SELL, 3, 10, "0.15"))
    with pytest.raises(LedgerError):
        book.record_fill(fill(SELL, 1, 10, "0.05", ticker="AIR.PA"))


def test_state_survives_reload(tmp_path):
    db = tmp_path / "ledger.db"
    a = Ledger(db)
    a.deposit("2026-11-11", 1000)
    a.record_fill(fill(BUY, 10, 50, "2.50"))
    b = Ledger(db)
    assert b.state() == a.state()


def test_books_are_isolated(tmp_path):
    agents = Ledger(tmp_path / "l.db", book="agents")
    bench = Ledger(tmp_path / "l.db", book="benchmark")
    agents.deposit("2026-11-11", 1000)
    assert bench.state().cash == 0


def test_snapshot_and_missing_mark(book):
    book.record_fill(fill(BUY, 10, 50, "2.50"))
    book.snapshot("2026-11-12", {"TTE.PA": 52})
    assert book.daily_values() == [("2026-11-12", D("1017.50"))]
    with pytest.raises(KeyError):
        book.snapshot("2026-11-13", {})


def test_rejection_is_logged(book):
    book.record_rejection("2026-11-12", "decision", Order("X.PA", BUY, 1), "test")
    rows = book.db.execute("SELECT ticker, reason FROM rejections").fetchall()
    assert rows == [("X.PA", "test")]
