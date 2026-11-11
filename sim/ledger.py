"""Comptabilité déterministe (pas de LLM).

Principe : on stocke uniquement des événements (dépôt, achat, vente, dividende) dans SQLite.
L'état du portefeuille est toujours recalculé en rejouant ces événements avec `apply_*`,
donc il n'existe qu'un seul chemin de calcul. Le PRU suit la méthode du coût moyen pondéré.
Plusieurs « livres » (agents, benchmark, aléatoire) partagent la même base.
"""
import sqlite3
from decimal import Decimal

from .models import BUY, SELL, Fill, Order, PortfolioState, Position
from .money import D, cents


class LedgerError(Exception):
    pass


# --- Logique pure ------------------------------------------------------------

def apply_deposit(state: PortfolioState, amount) -> None:
    amount = cents(amount)
    if amount <= 0:
        raise LedgerError("dépôt non positif")
    state.cash += amount
    state.deposits += amount


def apply_fill(state: PortfolioState, fill: Fill) -> None:
    if fill.qty <= 0:
        raise LedgerError(f"quantité invalide : {fill.qty}")
    pos = state.positions.get(fill.ticker, Position())

    if fill.side == BUY:
        cost = fill.amount + fill.fees
        if cost > state.cash:
            raise LedgerError(f"cash négatif interdit : coût {cost} > cash {state.cash}")
        state.cash -= cost
        pos.qty += fill.qty
        pos.cost_basis += cost
        state.positions[fill.ticker] = pos
    elif fill.side == SELL:
        if fill.qty > pos.qty:
            raise LedgerError(f"vente à découvert interdite : {fill.ticker} détenu {pos.qty}, vendu {fill.qty}")
        net = fill.amount - fill.fees
        if state.cash + net < 0:
            raise LedgerError("cash négatif interdit après frais de vente")
        cost_part = pos.cost_basis if fill.qty == pos.qty else cents(pos.cost_basis * fill.qty / pos.qty)
        state.cash += net
        state.realized_pnl += net - cost_part
        pos.qty -= fill.qty
        pos.cost_basis -= cost_part
        if pos.qty == 0:
            state.positions.pop(fill.ticker, None)
        else:
            state.positions[fill.ticker] = pos
    else:
        raise LedgerError(f"sens inconnu : {fill.side}")
    state.fees_paid += fill.fees


def apply_dividend(state: PortfolioState, ticker: str, per_share) -> Decimal:
    qty = state.qty(ticker)
    amount = cents(D(per_share) * qty)
    state.cash += amount
    state.dividends += amount
    return amount


# --- Persistance -------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book TEXT NOT NULL, date TEXT NOT NULL, type TEXT NOT NULL,
    ticker TEXT, qty INTEGER, price TEXT, ref_price TEXT, fees TEXT, amount TEXT
);
CREATE TABLE IF NOT EXISTS rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book TEXT NOT NULL, date TEXT NOT NULL, stage TEXT NOT NULL,
    ticker TEXT, side TEXT, qty INTEGER, reason TEXT
);
CREATE TABLE IF NOT EXISTS daily_values (
    book TEXT NOT NULL, date TEXT NOT NULL,
    cash TEXT, positions_value TEXT, total TEXT, fees_paid TEXT,
    PRIMARY KEY (book, date)
);
"""


class Ledger:
    def __init__(self, db_path, book: str = "agents"):
        self.book = book
        self.db = sqlite3.connect(db_path)
        self.db.executescript(SCHEMA)

    def _insert(self, date, type_, ticker=None, qty=None, price=None, ref_price=None, fees=None, amount=None):
        self.db.execute(
            "INSERT INTO events (book, date, type, ticker, qty, price, ref_price, fees, amount)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (self.book, date, type_, ticker, qty,
             *(None if v is None else str(v) for v in (price, ref_price, fees, amount))),
        )
        self.db.commit()

    # Chaque écriture est d'abord appliquée à l'état courant : une opération invalide lève
    # une LedgerError et n'est jamais enregistrée.
    def deposit(self, date: str, amount) -> None:
        apply_deposit(self.state(), amount)
        self._insert(date, "DEPOSIT", amount=cents(amount))

    def record_fill(self, fill: Fill) -> None:
        apply_fill(self.state(), fill)
        self._insert(fill.date, fill.side, fill.ticker, fill.qty, fill.price, fill.ref_price, fill.fees, fill.amount)

    def record_dividend(self, date: str, ticker: str, per_share) -> Decimal:
        amount = apply_dividend(self.state(), ticker, per_share)
        if amount > 0:
            self._insert(date, "DIVIDEND", ticker, self.state().qty(ticker), D(per_share), amount=amount)
        return amount

    def record_rejection(self, date: str, stage: str, order: Order, reason: str) -> None:
        self.db.execute(
            "INSERT INTO rejections (book, date, stage, ticker, side, qty, reason) VALUES (?,?,?,?,?,?,?)",
            (self.book, date, stage, order.ticker, order.side, order.qty if isinstance(order.qty, int) else None, reason),
        )
        self.db.commit()

    def state(self) -> PortfolioState:
        state = PortfolioState()
        rows = self.db.execute(
            "SELECT date, type, ticker, qty, price, ref_price, fees, amount FROM events WHERE book=? ORDER BY id",
            (self.book,),
        )
        for date, type_, ticker, qty, price, ref_price, fees, amount in rows:
            if type_ == "DEPOSIT":
                apply_deposit(state, D(amount))
            elif type_ in (BUY, SELL):
                apply_fill(state, Fill(date, ticker, type_, qty, D(price), D(ref_price), D(fees), D(amount)))
            elif type_ == "DIVIDEND":
                state.cash += D(amount)
                state.dividends += D(amount)
        return state

    def has_event(self, type_: str, ticker: str, date: str) -> bool:
        return self.db.execute("SELECT 1 FROM events WHERE book=? AND type=? AND ticker=? AND date=?",
                               (self.book, type_, ticker, date)).fetchone() is not None

    def last_snapshot_date(self) -> str | None:
        return self.db.execute("SELECT MAX(date) FROM daily_values WHERE book=?", (self.book,)).fetchone()[0]

    def rejections(self) -> list[dict]:
        rows = self.db.execute("SELECT date, stage, ticker, side, qty, reason FROM rejections WHERE book=? ORDER BY id",
                               (self.book,))
        return [dict(zip(("date", "stage", "ticker", "side", "qty", "reason"), r)) for r in rows]

    def start_date(self) -> str | None:
        row = self.db.execute("SELECT MIN(date) FROM events WHERE book=? AND type='DEPOSIT'", (self.book,)).fetchone()
        return row[0]

    def fills(self) -> list[Fill]:
        rows = self.db.execute(
            "SELECT date, ticker, type, qty, price, ref_price, fees, amount FROM events"
            " WHERE book=? AND type IN ('BUY','SELL') ORDER BY id", (self.book,))
        return [Fill(d, t, s, q, D(p), D(r), D(f), D(a)) for d, t, s, q, p, r, f, a in rows]

    def snapshot(self, date: str, marks: dict) -> dict:
        """Valorise le portefeuille aux cours `marks` et enregistre la valeur du jour."""
        s = summary(self.state(), marks)
        self.db.execute(
            "INSERT OR REPLACE INTO daily_values (book, date, cash, positions_value, total, fees_paid)"
            " VALUES (?,?,?,?,?,?)",
            (self.book, date, str(s["cash"]), str(s["positions_value"]), str(s["total"]), str(s["fees_paid"])),
        )
        self.db.commit()
        return s

    def daily_values(self) -> list[tuple[str, Decimal]]:
        rows = self.db.execute("SELECT date, total FROM daily_values WHERE book=? ORDER BY date", (self.book,))
        return [(d, D(t)) for d, t in rows]


def round_trips(fills: list[Fill]) -> list[dict]:
    """Regroupe les exécutions en positions : de la première ouverture jusqu'au retour à zéro.
    Une position encore ouverte est renvoyée avec closed=False."""
    open_: dict[str, dict] = {}
    trips = []
    for f in fills:
        t = open_.setdefault(f.ticker, {
            "trade_id": f"{f.ticker}-{f.date}", "ticker": f.ticker, "open_date": f.date, "close_date": None,
            "qty_bought": 0, "qty_sold": 0, "bought": Decimal("0"), "sold": Decimal("0"), "fees": Decimal("0"),
            "held": 0, "closed": False,
        })
        t["fees"] += f.fees
        if f.side == BUY:
            t["qty_bought"] += f.qty
            t["bought"] += f.amount
            t["held"] += f.qty
        else:
            t["qty_sold"] += f.qty
            t["sold"] += f.amount
            t["held"] -= f.qty
        if t["held"] == 0:
            t.update(closed=True, close_date=f.date)
            trips.append(open_.pop(f.ticker))
    trips += list(open_.values())
    for t in trips:
        t["avg_buy"] = (t["bought"] / t["qty_bought"]).quantize(Decimal("0.0001"))
        t["avg_sell"] = (t["sold"] / t["qty_sold"]).quantize(Decimal("0.0001")) if t["qty_sold"] else None
        if t["closed"]:
            t["pnl"] = t["sold"] - t["bought"] - t["fees"]
            t["pnl_pct"] = (t["pnl"] / t["bought"] * 100).quantize(Decimal("0.01"))
    return trips


def summary(state: PortfolioState, marks: dict) -> dict:
    positions_value = cents(state.positions_value(marks))
    total = state.cash + positions_value
    cost = sum((p.cost_basis for p in state.positions.values()), Decimal("0"))
    return {
        "cash": state.cash,
        "positions_value": positions_value,
        "total": total,
        "deposits": state.deposits,
        "return_pct": (total / state.deposits - 1) * 100 if state.deposits else Decimal("0"),
        "realized_pnl": state.realized_pnl,
        "unrealized_pnl": positions_value - cost,
        "fees_paid": state.fees_paid,
        "dividends": state.dividends,
    }
