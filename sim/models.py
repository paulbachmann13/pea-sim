"""Structures de données partagées."""
from dataclasses import dataclass, field
from decimal import Decimal

BUY, SELL = "BUY", "SELL"


@dataclass(frozen=True)
class Order:
    ticker: str
    side: str          # BUY | SELL
    qty: int
    rationale: str = ""


@dataclass(frozen=True)
class Instrument:
    """Données de référentiel nécessaires à la validation d'un achat."""
    ticker: str
    isin: str | None
    currency: str | None
    kind: str                                  # "equity" | "etf"
    avg_daily_volume_eur: Decimal | None = None


@dataclass(frozen=True)
class Fill:
    date: str          # AAAA-MM-JJ
    ticker: str
    side: str
    qty: int
    price: Decimal     # prix d'exécution, spread inclus
    ref_price: Decimal  # cours de référence (ouverture)
    fees: Decimal
    amount: Decimal    # qty × price, arrondi au centime (hors frais)


@dataclass
class Position:
    qty: int = 0
    cost_basis: Decimal = Decimal("0")   # coût total frais d'achat inclus

    @property
    def pru(self) -> Decimal:
        return self.cost_basis / self.qty if self.qty else Decimal("0")


@dataclass
class PortfolioState:
    cash: Decimal = Decimal("0")
    positions: dict[str, Position] = field(default_factory=dict)
    deposits: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")
    dividends: Decimal = Decimal("0")

    def qty(self, ticker: str) -> int:
        pos = self.positions.get(ticker)
        return pos.qty if pos else 0

    def positions_value(self, marks: dict) -> Decimal:
        missing = [t for t in self.positions if t not in marks]
        if missing:
            raise KeyError(f"pas de cours pour valoriser : {missing}")
        return sum((Decimal(str(marks[t])) * p.qty for t, p in self.positions.items()), Decimal("0"))

    def total_value(self, marks: dict) -> Decimal:
        return self.cash + self.positions_value(marks)
