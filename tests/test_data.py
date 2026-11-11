import pandas as pd
import pytest

from sim import market_calendar as mc
from sim.data import PriceStore, check_bars, parse_euronext_history, update_prices
from sim.money import D


def frame(rows):
    """rows : {date: (open, high, low, close, volume)}"""
    df = pd.DataFrame.from_dict(rows, orient="index", columns=["open", "high", "low", "close", "volume"])
    df["adj_close"] = df["close"]
    return df[["open", "high", "low", "close", "adj_close", "volume"]].sort_index()


GOOD = {
    "2026-11-09": (10, 10.5, 9.8, 10.2, 1000),
    "2026-11-10": (10.2, 10.4, 10.0, 10.1, 1200),
    "2026-11-11": (10.1, 10.6, 10.1, 10.5, 900),
}


@pytest.fixture
def store():
    return PriceStore(":memory:")


# --- Calendrier ---------------------------------------------------------------

def test_calendar():
    assert mc.is_trading_day("2026-11-11")            # 11 novembre : Euronext ouvert
    assert not mc.is_trading_day("2026-12-25")
    assert not mc.is_trading_day("2026-11-14")        # samedi
    assert mc.next_session("2026-11-13") == "2026-11-16"
    assert mc.previous_session("2026-11-16") == "2026-11-13"
    assert mc.next_session("2026-12-24") == "2026-12-28"
    assert mc.sessions_between("2026-11-13", "2026-11-16") == ["2026-11-13", "2026-11-16"]


# --- Contrôle de cohérence ----------------------------------------------------

def test_check_bars_clean():
    assert check_bars("X", frame(GOOD), ["2026-11-11"], 25) == []


@pytest.mark.parametrize("row, detail", [
    ((10, 9, 9.5, 9.8, 100), "incohérent"),            # high < low
    ((12, 10.6, 10.1, 10.5, 100), "incohérent"),       # open hors [low, high]
    ((0, 10.6, 10.1, 10.5, 100), "nul"),
    ((float("nan"), 10.6, 10.1, 10.5, 100), "manquante"),
    ((10.1, 10.6, 10.1, 10.5, -5), "volume"),
])
def test_check_bars_invalid(row, detail):
    issues = check_bars("X", frame({**GOOD, "2026-11-11": row}), [], 25)
    assert any(i.kind == "invalid" and i.blocking and detail in i.detail for i in issues)


def test_check_bars_missing_and_jump():
    rows = {**GOOD, "2026-11-11": (14, 14.5, 13.8, 14.0, 900)}   # +38,6 %
    issues = check_bars("X", frame(rows), ["2026-11-11", "2026-11-12"], 25)
    kinds = {(i.kind, i.date, i.blocking) for i in issues}
    assert ("missing", "2026-11-12", True) in kinds
    assert ("jump", "2026-11-11", False) in kinds


# --- Stockage et absence de look-ahead ---------------------------------------

def test_store_never_returns_future(store):
    store.upsert_bars("X", frame(GOOD), "test")
    assert list(store.bars("X", "2026-11-10").index) == ["2026-11-09", "2026-11-10"]
    assert store.last_close("X", "2026-11-10") == D("10.1")
    assert store.bar("X", "2026-11-12") is None
    assert store.price("X", "2026-11-11", "open") == D("10.1")
    assert store.marks(["X", "Y"], "2026-11-09") == {"X": D("10.2")}


def test_avg_daily_volume_eur(store):
    store.upsert_bars("X", frame(GOOD), "test")
    assert store.avg_daily_volume_eur("X", "2026-11-11", window=20) is None   # trop peu d'historique
    rows = {f"2026-10-{d:02d}": (10, 11, 9, 10, 1000) for d in range(1, 21)}
    store.upsert_bars("Y", frame(rows), "test")
    assert store.avg_daily_volume_eur("Y", "2026-10-20", window=20) == D("10000")
    assert store.avg_daily_volume_eur("Y", "2026-10-05", window=20) is None


# --- update_prices avec sources simulées ---------------------------------------

def test_update_prices_fills_gap_from_euronext(store, cfg):
    yf_rows = {k: v for k, v in GOOD.items() if k != "2026-11-11"}
    fake_yf = lambda tickers, s, e: ({"X": frame(yf_rows)}, {"X": {"2026-11-10": 0.5}})
    fake_enx = lambda isin, mic, s, e: frame(GOOD)
    issues = update_prices(store, ["X"], {"X": ("FR0000000001", "XPAR")}, "2026-11-09", "2026-11-11",
                           ["2026-11-11"], cfg, fetch_yf=fake_yf, fetch_enx=fake_enx)
    assert store.price("X", "2026-11-11") == D("10.5")
    assert not any(i.blocking for i in issues)
    assert store.dividends_on("2026-11-10") == {"X": D("0.5")}
    src = store.db.execute("SELECT source FROM bars WHERE date='2026-11-11'").fetchone()[0]
    assert src == "euronext"


def test_update_prices_rejects_bad_bar_and_cross_checks(store, cfg):
    bad = {**GOOD, "2026-11-11": (10, 9, 9.5, 9.8, 100)}
    fake_yf = lambda tickers, s, e: ({"X": frame(bad), "Y": frame(GOOD)}, {})
    enx_y = {**GOOD, "2026-11-11": (10.1, 10.8, 10.1, 10.7, 900)}           # écart 1,9 %
    fake_enx = lambda isin, mic, s, e: frame(enx_y) if isin == "Y" else pd.DataFrame()
    issues = update_prices(store, ["X", "Y"], {"Y": ("Y", "XPAR")}, "2026-11-09", "2026-11-11",
                           ["2026-11-11"], cfg, cross_check={"Y"}, fetch_yf=fake_yf, fetch_enx=fake_enx)
    assert store.bar("X", "2026-11-11") is None                              # barre rejetée, non stockée
    assert any(i.ticker == "X" and i.kind == "invalid" and i.blocking for i in issues)
    assert any(i.ticker == "Y" and i.kind == "cross_check" for i in issues)
    assert store.db.execute("SELECT COUNT(*) FROM data_issues").fetchone()[0] == len(issues)


def test_update_prices_survives_yfinance_outage(store, cfg):
    def broken(*a):
        raise ConnectionError("down")
    issues = update_prices(store, ["X"], {"X": ("I", "XPAR")}, "2026-11-09", "2026-11-11", ["2026-11-11"],
                           cfg, fetch_yf=broken, fetch_enx=lambda *a: frame(GOOD))
    assert any(i.kind == "source_error" for i in issues)
    assert store.price("X", "2026-11-11") == D("10.5")


def test_parse_euronext_history():
    text = ('﻿"Historical Data"\n"From 2026-10-20 to 2026-11-11"\nFR0000120271\n'
            'Date;Open;High;Low;Last;Close;"Number of Shares";"Number of Trades";Turnover;vwap\n'
            '11/11/2026;78.50;79.59;78.50;79.37;79.37;2379843;15875;188732888;79.3048\n'
            '10/11/2026;78.00;78.45;77.19;78.20;78.20;4818810;31717;375632502;77.9513\n')
    df = parse_euronext_history(text)
    assert list(df.index) == ["2026-11-10", "2026-11-11"]
    assert df.at["2026-11-11", "open"] == 78.5 and df.at["2026-11-11", "volume"] == 2379843
