import copy

import pytest

from sim.config import fee_grid, load_config
from sim.ledger import Ledger
from sim.models import Instrument
from sim.money import D


@pytest.fixture
def cfg():
    cfg = copy.deepcopy(load_config(local=None))
    cfg["supabase"]["enabled"] = False
    return cfg


@pytest.fixture
def grid(cfg):
    return fee_grid(cfg, "bforbank")


@pytest.fixture
def ledger():
    return Ledger(":memory:")


def equity(ticker="TTE.PA", isin="FR0000120271", currency="EUR", volume=500_000_000):
    return Instrument(ticker, isin, currency, "equity", None if volume is None else D(volume))


@pytest.fixture
def instruments():
    return {
        "TTE.PA": equity(),
        "AIR.PA": equity("AIR.PA", "NL0000235190"),
        "ASML.AS": equity("ASML.AS", "NL0010273215"),
        "WPEA.PA": Instrument("WPEA.PA", "IE0002XZSHO1", "EUR", "etf"),
    }
