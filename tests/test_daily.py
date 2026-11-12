"""Simulation complète de bout en bout avec sources et LLM factices."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from sim import daily
from sim import market_calendar as mc
from sim.data import PriceStore
from sim.llm import LLMError, ask
from sim.models import Instrument
from sim.money import D
from sim.news import NewsItem
from sim.universe import RefEntry
from test_agents_decide import MARKET, NEWS, FakeLLM, chef_with, risk_all

PARIS = ZoneInfo("Europe/Paris")
DAYS = mc.sessions_between("2026-08-03", "2026-12-31")
TICKERS = {"AAA.PA": 10, "BBB.PA": 20, "CCC.PA": 15, "DDD.PA": 30, "EEE.PA": 25, "WPEA.PA": 7}

REPORT = {"verdict": "v", "what_worked": ["a"], "what_failed": ["b"], "fees_impact": "f", "process": "p",
          "lessons": ["l"]}
PM = lambda p: {"postmortems": [{"trade_id": "AAA.PA-2026-11-17", "expected": "e", "happened": "h",
                                 "predictable": "p", "lesson": "l",
                                 "playbook_ops": [{"op": "add", "rule_id": "", "text": "Si rebond rapide, alors vendre",
                                                   "scope": "chef", "origin": "x", "reason": "r"}]}]}
REVIEW = {"review": "r", "decisions": [], "playbook_ops": [], "insights": []}
WEEKLY = {"summary": "s", "recurring_errors": [], "biases": [], "playbook_ops": [], "insights": [],
          "obsolete_insights": [], "notes_updates": []}


def at(d, h=18):
    return datetime.fromisoformat(d).replace(hour=h, minute=0, tzinfo=PARIS)


def bars(start):
    closes = [start + 0.02 * i for i in range(len(DAYS))]
    opens = [c - 0.01 for c in closes]
    return pd.DataFrame({"open": opens, "high": [c * 1.01 for c in closes], "low": [o * 0.99 for o in opens],
                         "close": closes, "adj_close": closes, "volume": [300_000] * len(DAYS)}, index=DAYS)


@pytest.fixture
def sim(tmp_path, cfg):
    store = PriceStore(tmp_path / "market.db")
    for t, p in TICKERS.items():
        store.upsert_bars(t, bars(p), "fake")
    store.upsert_dividends("AAA.PA", {"2026-11-18": 0.5})
    universe = {t: RefEntry(t, "FR0000000001", t.split(".")[0], "Euronext Paris", "XPAR", "EUR") for t in TICKERS}

    def refresh(cfg, as_of, held, root):
        inst = {t: Instrument(t, "FR0000000001", "EUR", "equity", store.avg_daily_volume_eur(t, as_of, 20))
                for t in TICKERS if t != "WPEA.PA"}
        inst["WPEA.PA"] = Instrument("WPEA.PA", None, "EUR", "etf")
        return store, universe, inst, []

    def chef(prompt):
        if "Décision du 2026-11-16" in prompt:
            return chef_with(("AAA.PA", "BUY", 20), source="n2026-11-16")
        if "Décision du 2026-11-17" in prompt:
            return chef_with(("AAA.PA", "SELL", 20), source="n2026-11-17")
        return chef_with()

    llm = FakeLLM(market=MARKET, news=NEWS, chef=chef, risk=risk_all(), postmortem=PM, report=REPORT,
                  review=REVIEW, weekly=WEEKLY)
    cfg["data"]["market_db"] = "market.db"
    (tmp_path / "data").mkdir()

    def go(d, h=18, **kw):
        news = lambda c, w: ([NewsItem(f"n{d}", "test", "AAA.PA", f"AAA : nouvelle du {d}", "",
                                       f"{d}T10:00:00+00:00", "")], [])
        return daily.run(at(d, h), cfg, tmp_path, llm=llm, refresh=refresh, fetch=news,
                         fetch_hist=lambda c, n, a: [], **kw)

    return {"go": go, "root": tmp_path, "llm": llm, "cfg": cfg}


def test_full_simulation(sim):
    go, root = sim["go"], sim["root"]
    env = daily.open_env(sim["cfg"], root)
    led = env.ledgers

    r = go("2026-11-16")                                            # J1 : démarrage + 1re décision
    assert r.mode == "trading" and "BUY 20 AAA.PA" in r.message
    assert all(l.state().cash == D(1000) for l in led.values())
    assert env.journal.load_pending()["exec_date"] == "2026-11-17"

    assert go("2026-11-16").mode == "skip"                          # idempotent

    r = go("2026-11-17")                                            # J2 : exécution à l'ouverture
    a, low = led["agents"].fills(), led["agents_lowcost"].fills()
    assert [(f.ticker, f.qty) for f in a] == [("AAA.PA", 20)] == [(f.ticker, f.qty) for f in low]
    assert low[0].fees < a[0].fees and low[0].price == a[0].price
    assert a[0].ref_price == D(str(bars(10).at["2026-11-17", "open"]))
    assert led["benchmark"].fills()[0].ticker == "WPEA.PA"
    rnd = led["random"].fills()
    assert len(rnd) == sim["cfg"]["simulation"]["random_picks"] and "WPEA.PA" not in {f.ticker for f in rnd}
    day = env.journal.read_day("2026-11-17")
    assert day["executions"][0]["filled"] and day["executions"][0]["qty"] == 20
    assert "Exécutés : BUY 20 AAA.PA" in r.message

    go("2026-11-18")                                                # J3 : vente à l'ouverture + dividende du jour
    st = led["agents"].state()
    assert "AAA.PA" not in st.positions and st.dividends == D("10.00")   # détenu à la clôture de la veille
    assert env.journal.postmortems()[0]["trade_id"] == "AAA.PA-2026-11-17"
    learn = env.journal.read_day("2026-11-18")["learning"]
    assert learn["postmortems"] == ["AAA.PA-2026-11-17"] and learn["playbook_changes"][0]["rule"] == "R1"
    assert [p["kind"] for p in env.kb.predictions()["items"]][:2] == ["ordre BUY", "ordre SELL"]

    r = go("2026-11-21", h=10)                                      # samedi : veille
    assert r.mode == "veille" and env.journal.day_path("2026-11-21", "veille").exists()

    go("2026-11-25")                                                # runs manqués du 19 au 24 : rattrapés
    vals = [d for d, _ in led["agents"].daily_values()]
    assert vals == ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-25"]

    r = go("2026-12-18")                                            # run du dernier jour (16/12) manqué
    assert r.mode == "final"
    report = json.loads((root / "journal" / "rapport-final.json").read_text(encoding="utf-8"))
    assert report["analysis"]["verdict"] == "v"
    assert report["stats"]["as_of"] == "2026-12-16"
    assert set(report["stats"]["books"]) == set(daily.BOOKS)
    assert env.journal.load_pending() is None

    assert go("2026-12-21").mode == "skip"                          # simulation terminée

    summary = json.loads((root / "docs" / "data" / "summary.json").read_text(encoding="utf-8"))
    assert set(summary["books"]) == set(daily.BOOKS) and summary["books"]["agents"]["series"]
    orders = json.loads((root / "docs" / "data" / "orders.json").read_text(encoding="utf-8"))
    assert orders["fills"][0]["thesis"] == "thèse AAA.PA"
    assert (root / "docs" / "data" / "report.json").exists()
    know = json.loads((root / "docs" / "data" / "knowledge.json").read_text(encoding="utf-8"))
    assert know["playbook"][0]["id"] == "R1" and know["calibration"]["n"] >= 2 and know["log"]
    index = json.loads((root / "docs" / "data" / "journal" / "index.json").read_text(encoding="utf-8"))
    assert index[0]["final"] and any(i["mode"] == "veille" for i in index)


def test_failed_llm_day_is_redone_by_the_catch_up_run(sim):
    """Quota Claude atteint au run de 18h : le rattrapage du même jour refait la décision."""
    go, llm, root = sim["go"], sim["llm"], sim["root"]
    env = daily.open_env(sim["cfg"], root)
    answers = dict(llm.answers)
    llm.answers = {**answers, "market": LLMError("quota"), "news": LLMError("quota"), "chef": LLMError("quota")}

    r = go("2026-11-16")
    assert r.mode == "trading" and r.warnings and env.journal.load_pending()["orders"] == []
    assert env.journal.last_run_at() is None                       # un run en erreur ne compte pas
    assert not env.journal.is_complete("2026-11-16")

    llm.answers = answers                                          # le quota est revenu
    r = go("2026-11-16")                                           # même jour, sans --force
    assert r.mode == "trading" and "BUY 20 AAA.PA" in r.message and not r.warnings
    assert all(l.state().deposits == D(1000) for l in env.ledgers.values())    # pas de second dépôt
    assert env.agents.daily_values() == [("2026-11-16", D(1000))]
    assert env.journal.load_pending()["orders"][0]["qty"] == 20
    assert go("2026-11-16").mode == "skip"                         # maintenant terminé


def test_quota_error_is_not_retried(cfg):
    from sim.llm import QuotaError
    calls = []

    def llm(*a):
        calls.append(1)
        raise QuotaError("quota")
    with pytest.raises(LLMError, match="chef : quota"):
        ask("chef", "P", "S", {}, cfg, llm=llm)
    assert len(calls) == 1


def test_lock_prevents_concurrent_runs(sim):
    (sim["root"] / "data" / "run.lock").write_text("123")
    with pytest.raises(RuntimeError, match="déjà en cours"):
        sim["go"]("2026-11-16")
