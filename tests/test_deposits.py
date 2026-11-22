import json
from decimal import Decimal

import pytest

from sim import daily
from sim.decide import deposits_text
from sim.deposit import add_deposit
from sim.ledger import Ledger
from sim.money import D
from test_daily import sim  # noqa: F401  (fixture de simulation complète)


def snaps(led, rows):
    for d, total in rows:
        led.db.execute("INSERT OR REPLACE INTO daily_values (book, date, cash, positions_value, total, fees_paid)"
                       " VALUES (?,?,?,?,?,?)", (led.book, d, "0", "0", str(total), "0"))
    led.db.commit()


# --- Performance indépendante des versements -----------------------------------------------

def test_time_weighted_return_ignores_deposits(ledger):
    ledger.deposit("2026-11-12", 1000)
    ledger.deposit("2026-11-16", 4000)                         # versement après la clôture du 16
    snaps(ledger, [("2026-11-12", 1000), ("2026-11-13", 990), ("2026-11-16", 4980), ("2026-11-17", "5029.80")])
    s = {p["date"]: p for p in ledger.performance_series()}
    assert s["2026-11-12"]["twr_pct"] == 0 and s["2026-11-16"]["flow"] == D(4000)
    assert s["2026-11-13"]["twr_pct"] == D("-1")
    assert s["2026-11-16"]["twr_pct"] == D("-2")               # 990 → 980 : −1,01 %, le versement ne compte pas
    assert round(s["2026-11-17"]["twr_pct"], 2) == D("-1.02")  # +1 % sur 4 980
    assert ledger.twr_last_pct() == s["2026-11-17"]["twr_pct"]


def test_weekend_deposit_enters_the_next_trading_day(ledger):
    ledger.deposit("2026-11-13", 1000)
    ledger.deposit("2026-11-14", 500)                          # samedi
    snaps(ledger, [("2026-11-13", 1000), ("2026-11-16", 1515)])
    assert ledger.performance_series()[-1]["flow"] == D(500)
    assert round(ledger.twr_last_pct(), 2) == D("1.5")


def test_without_deposits_it_is_the_simple_return(ledger):
    ledger.deposit("2026-11-12", 1000)
    snaps(ledger, [("2026-11-12", 1000), ("2026-11-13", 1010)])
    assert ledger.twr_last_pct() == D("1")
    assert ledger.snapshot("2026-11-13", {})["return_pct"] == ledger.twr_last_pct()      # même valeur que la série


def test_deposits_text_only_after_a_second_deposit(ledger):
    ledger.deposit("2026-11-12", 1000)
    assert deposits_text(ledger) == ""
    ledger.deposit("2026-11-16", 4000)
    txt = deposits_text(ledger)
    assert "1000.00 € le 2026-11-12" in txt and "4000.00 € le 2026-11-16" in txt and "total déposé 5000.00 €" in txt
    assert "garder du cash reste un choix légitime" in txt


# --- Versement en cours de simulation : équité entre les quatre portefeuilles ----------------

def test_mid_simulation_deposit(sim):
    go, root, llm, store = sim["go"], sim["root"], sim["llm"], sim["store"]
    env = daily.open_env(sim["cfg"], root)
    go("2026-11-16")
    go("2026-11-17")
    before = {b: l.twr_last_pct() for b, l in env.ledgers.items()}
    nb_bench = len(env.ledgers["benchmark"].fills())

    add_deposit(env, store, 4000, "2026-11-17")                          # après le run du 17 : valeur recalculée
    assert all(l.state().deposits == D(5000) for l in env.ledgers.values())
    for b, l in env.ledgers.items():
        assert l.performance_series()[-1]["flow"] == D(4000)
        assert l.twr_last_pct() == before[b]                             # le versement ne change aucune performance
    assert env.agents.state().cash >= D(4000)

    go("2026-11-18")                                                     # séance suivante : on investit
    bench = env.ledgers["benchmark"].fills()
    assert len(bench) == nb_bench + 1 and bench[-1].date == "2026-11-18" and bench[-1].amount > D(3900)
    assert env.ledgers["benchmark"].state().cash < D(50)                  # tout est investi, à la miette près
    rnd = env.ledgers["random"].fills()
    assert {f.date for f in rnd} == {"2026-11-17", "2026-11-18"}         # nouveau lot, tiré au sort, pas avant
    assert sum(f.amount for f in rnd if f.date == "2026-11-18") > D(3000)  # le nouveau lot emploie bien le versement
    large = env.ledgers["random_large"].fills()                          # 2e aléatoire : lot par versement, grille low-cost
    assert {f.date for f in large} == {"2026-11-17", "2026-11-18"} and all(f.fees < D("2.5") for f in large)
    assert len({f.ticker for f in large if f.date == "2026-11-18"}) == min(5, sim["cfg"]["simulation"]["random_large_picks"])
    chef_prompt = [p for r, p in llm.calls if r == "chef"][-1]
    assert "1000.00 € le 2026-11-16; 4000.00 € le 2026-11-17" in chef_prompt and "total déposé 5000.00 €" in chef_prompt

    n_fills = {b: len(l.fills()) for b, l in env.ledgers.items() if b in ("benchmark", "random")}
    go("2026-11-19")                                                     # le reste de cash n'est pas réinvesti
    assert {b: len(env.ledgers[b].fills()) for b in n_fills} == n_fills

    summary = json.loads((root / "docs" / "data" / "summary.json").read_text(encoding="utf-8"))
    books = summary["books"]
    assert summary["capital"] == 5000 and books["agents"]["deposits"] == 5000
    assert books["agents"]["deposit_events"][-1] == {"date": "2026-11-17", "amount": 4000.0}
    assert any(p["flow"] == 4000 for p in books["benchmark"]["series"])
    assert all("twr" in p for p in books["agents"]["series"])


def test_deposit_is_guarded(sim):
    env = daily.open_env(sim["cfg"], sim["root"])
    sim["go"]("2026-11-16")
    add_deposit(env, sim["store"], 4000, "2026-11-16")
    with pytest.raises(ValueError, match="existe déjà"):
        add_deposit(env, sim["store"], 4000, "2026-11-16")
    add_deposit(env, sim["store"], 4000, "2026-11-16", force=True)
    assert env.agents.state().deposits == D(9000)
    with pytest.raises(ValueError, match="positif"):
        add_deposit(env, sim["store"], -5, "2026-11-17")


def test_resumed_run_rebuilds_executions_from_the_ledger(sim):
    go, root = sim["go"], sim["root"]
    env = daily.open_env(sim["cfg"], root)
    go("2026-11-16")
    go("2026-11-17")
    ex = daily.fills_as_executions(env.agents, "2026-11-17")
    assert [(e["ticker"], e["side"], e["qty"], e["filled"]) for e in ex] == [("AAA.PA", "BUY", 20, True)]
    assert daily.fills_as_executions(env.agents, "2026-11-16") == []


def test_monthly_table_separates_market_from_team_choices(tmp_path, cfg):
    from sim.report import monthly_table
    books = {b: Ledger(tmp_path / "l.db", b) for b in ("agents", "benchmark", "random", "random_large")}
    for led in books.values():
        led.deposit("2026-11-12", 1000)
    snaps(books["agents"], [("2026-11-12", 1000), ("2026-12-11", 1100), ("2026-12-14", 1155)])
    snaps(books["benchmark"], [("2026-11-12", 1000), ("2026-12-11", 1050), ("2026-12-14", 1050)])
    for b in ("random", "random_large"):
        snaps(books[b], [("2026-11-12", 1000), ("2026-12-11", 900), ("2026-12-14", 900)])
    m1, m2 = monthly_table(cfg, books, "2026-12-14")
    assert (m1["month"], m1["to"], m1["partial"]) == (1, "2026-12-11", False)
    assert m1["books"]["agents"] == 10 and m1["vs_etf"] == 5 and m1["vs_random"] == 20
    assert (m2["month"], m2["from"], m2["partial"]) == (2, "2026-12-11", True)
    assert m2["books"]["agents"] == 5 and m2["books"]["benchmark"] == 0 and m2["vs_etf"] == 5
