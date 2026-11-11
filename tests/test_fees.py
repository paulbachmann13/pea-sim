import pytest

from sim.config import ConfigError, check_config, fee_grid
from sim.fees import compute_fees
from sim.money import D


@pytest.mark.parametrize("amount, expected", [
    (300, "1.50"),       # plafond PEA 0,50 % < 2,50 €
    (600, "2.50"),       # plafond 3,00 € > 2,50 €
    (1000, "2.50"),      # borne incluse du 1er palier
    (1000.01, "5.00"),   # 2e palier 6 €, plafonné à 0,50 %
    (2000, "6.00"),
    (3000, "6.00"),
    (5000, "12.00"),     # 0,15 % = 7,50 € < minimum 12 €
    (10000, "15.00"),    # 0,15 %
])
def test_bforbank_grid(grid, amount, expected):
    assert compute_fees(amount, grid) == D(expected)


def test_lowcost_grid(cfg):
    low = fee_grid(cfg, "lowcost")
    assert compute_fees(50, low) == D("0.25")     # plafond PEA
    assert compute_fees(5000, low) == D("1.00")


@pytest.mark.parametrize("amount", [0, -10])
def test_invalid_amount(grid, amount):
    with pytest.raises(ValueError):
        compute_fees(amount, grid)


def test_fees_never_decrease_with_amount(grid):
    # Indispensable pour la recherche dichotomique de quantité max.
    prev = D(0)
    for cents_ in range(1, 2_000_000, 997):
        fee = compute_fees(D(cents_) / 100, grid)
        assert fee >= prev
        prev = fee


def test_config_rejects_capped_last_tier(cfg):
    cfg["fees"]["grids"]["bforbank"]["tiers"][-1]["up_to"] = 50000
    with pytest.raises(ConfigError):
        check_config(cfg)


def test_config_rejects_unknown_active_grid(cfg):
    cfg["fees"]["active"] = "inconnue"
    with pytest.raises(ConfigError):
        check_config(cfg)
