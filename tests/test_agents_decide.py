from datetime import datetime, timezone

import pandas as pd
import pytest

from sim import agents
from sim import market_calendar as mc
from sim.data import PriceStore
from sim.decide import horizon, run_decision, run_postmortems, run_veille
from sim.journal import Journal, digest
from sim.ledger import Ledger, round_trips
from sim.llm import LLMError, ask, resolve_claude_bin
from sim.models import BUY, SELL, Fill, Instrument
from sim.money import D

DAYS = mc.sessions_between("2026-08-03", "2026-11-11")
AS_OF = DAYS[-1]
RUN_AT = datetime(2026, 11, 11, 17, 0, tzinfo=timezone.utc)


class FakeLLM:
    """Répond selon le schéma demandé ; `answers[role]` = dict ou exception."""
    ROLE = {id(agents.MARKET_SCHEMA): "market", id(agents.NEWS_SCHEMA): "news", id(agents.CHEF_SCHEMA): "chef",
            id(agents.RISK_SCHEMA): "risk", id(agents.POSTMORTEM_SCHEMA): "postmortem",
            id(agents.REPORT_SCHEMA): "report"}

    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    def __call__(self, prompt, system, model, schema, cfg):
        role = self.ROLE[id(schema)]
        self.calls.append((role, prompt))
        a = self.answers[role]
        a = a(prompt) if callable(a) else a
        if isinstance(a, Exception):
            raise a
        return a, {"model": model, "duration_s": 0, "input_tokens": 1, "output_tokens": 1}


MARKET = {"summary": "marché calme", "opportunities": [], "positions": [], "warnings": []}
NEWS = {"summary": "rien", "by_company": [], "held_alerts": [], "context_notes": "notes v2"}


def chef_with(*orders):
    return {"market_view": "haussier", "no_trade_reason": "",
            "orders": [{"ticker": t, "side": s, "qty": q, "thesis": f"thèse {t}", "sources": [], "exit_plan": "+5 %"}
                       for t, s, q in orders]}


def risk_all(decision="OK"):
    def answer(prompt):
        block = prompt.split("## Ordres à examiner\n")[1].split("\n\n## ")[0]
        n = sum(1 for line in block.splitlines() if line.startswith("["))
        return {"comment": "", "reviews": [{"index": i, "decision": decision, "reason": "r"} for i in range(n)]}
    return answer


def bars(start, n=len(DAYS)):
    closes = [start + 0.05 * i for i in range(n)]
    return pd.DataFrame({"open": closes, "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
                         "close": closes, "adj_close": closes, "volume": [200_000] * n}, index=DAYS[-n:])


@pytest.fixture
def world(tmp_path):
    store = PriceStore(":memory:")
    for t, p in [("AAA.PA", 10), ("BBB.PA", 20), ("WPEA.PA", 6)]:
        store.upsert_bars(t, bars(p), "t")
    inst = {t: Instrument(t, "FR0000000001", "EUR", "equity", store.avg_daily_volume_eur(t, AS_OF, 20))
            for t in ["AAA.PA", "BBB.PA"]}
    inst["WPEA.PA"] = Instrument("WPEA.PA", None, "EUR", "etf")
    inst["USA.PA"] = Instrument("USA.PA", "US0000000001", "EUR", "equity", D(10**9))
    store.upsert_bars("USA.PA", bars(10), "t")
    ledger = Ledger(tmp_path / "l.db")
    ledger.deposit("2026-11-02", 1000)
    return {"store": store, "instruments": inst, "ledger": ledger, "journal": Journal(tmp_path / "journal")}


def decide(world, cfg, llm):
    return run_decision(as_of=AS_OF, run_at=RUN_AT, cfg=cfg, llm=llm, news_items=[], **world)


# --- llm.ask --------------------------------------------------------------------

def test_ask_retries_with_feedback(cfg):
    replies = iter([({"orders": [1] * 9}, {}), ({"orders": []}, {})])
    prompts = []

    def llm(prompt, *a):
        prompts.append(prompt)
        return next(replies)

    def check(obj):
        if len(obj["orders"]) > 5:
            raise ValueError("trop d'ordres")
    obj, metas = ask("chef", "P", "S", {}, cfg, check=check, llm=llm)
    assert obj == {"orders": []} and len(metas) == 2
    assert "trop d'ordres" in prompts[1]


def test_ask_gives_up(cfg):
    def llm(*a):
        raise LLMError("down")
    with pytest.raises(LLMError, match="chef"):
        ask("chef", "P", "S", {}, cfg, llm=llm)


def test_resolve_claude_bin_explicit():
    assert resolve_claude_bin(r"C:\x\claude.exe") == r"C:\x\claude.exe"


def test_rules_text_mentions_limits(cfg):
    txt = agents.rules_text(cfg)
    assert "5 ordres" in txt and "40 %" in txt and "2.50 €" in txt and "WPEA.PA" in txt


# --- run_decision ---------------------------------------------------------------

def test_decision_happy_path(world, cfg):
    llm = FakeLLM(market=MARKET, news=NEWS, chef=chef_with(("AAA.PA", BUY, 20), ("USA.PA", BUY, 5)),
                  risk=risk_all("OK"))
    entry = decide(world, cfg, llm)
    assert [c[0] for c in llm.calls] == ["market", "news", "chef", "risk"]
    assert [o["ticker"] for o in entry["final_orders"]] == ["AAA.PA"]
    assert entry["refused"][0]["ticker"] == "USA.PA" and "UE/EEE" in entry["refused"][0]["reason"]
    pending = world["journal"].load_pending()
    assert pending["exec_date"] == "2026-11-12" and pending["orders"][0]["qty"] == 20
    assert world["journal"].context_notes() == "notes v2"
    assert world["journal"].read_day(AS_OF)["chef"]["market_view"] == "haussier"
    assert "Leçons" in llm.calls[2][1] and "Screener" in llm.calls[2][1]
    assert entry["errors"] == []


def test_decision_reduces_oversized_order(world, cfg):
    llm = FakeLLM(market=MARKET, news=NEWS, chef=chef_with(("AAA.PA", BUY, 500)), risk=risk_all())
    entry = decide(world, cfg, llm)
    o = entry["final_orders"][0]
    assert 0 < o["qty"] < 500 and "réduite" in o["validation_note"]


def test_risk_can_block(world, cfg):
    llm = FakeLLM(market=MARKET, news=NEWS, chef=chef_with(("AAA.PA", BUY, 10)), risk=risk_all("BLOCK"))
    entry = decide(world, cfg, llm)
    assert entry["final_orders"] == [] and entry["refused"][0]["by"] == "risque"
    assert world["ledger"].db.execute("SELECT stage FROM rejections").fetchall() == [("risk",)]


def test_risk_outage_blocks_everything(world, cfg):
    llm = FakeLLM(market=MARKET, news=NEWS, chef=chef_with(("AAA.PA", BUY, 10)), risk=LLMError("down"))
    entry = decide(world, cfg, llm)
    assert entry["final_orders"] == [] and any("risque" in e for e in entry["errors"])


def test_chef_outage_means_no_orders(world, cfg):
    llm = FakeLLM(market=LLMError("x"), news=NEWS, chef=LLMError("down"), risk=risk_all())
    entry = decide(world, cfg, llm)
    assert entry["final_orders"] == [] and len(entry["errors"]) == 2
    assert world["journal"].load_pending()["orders"] == []
    assert "risk" not in [c[0] for c in llm.calls]


def test_chef_too_many_orders_is_retried(world, cfg):
    answers = iter([chef_with(*[("AAA.PA", BUY, 1)] * 7), chef_with(("AAA.PA", BUY, 1))])
    llm = FakeLLM(market=MARKET, news=NEWS, chef=lambda p: next(answers), risk=risk_all())
    entry = decide(world, cfg, llm)
    assert len(entry["final_orders"]) == 1


def test_horizon(world, cfg):
    h = horizon(world["ledger"], AS_OF, cfg)
    assert h["start"] == "2026-11-02" and h["end"] == "2026-12-02" and h["exec_date"] == "2026-11-12"
    assert h["session_no"] == 8 and h["sessions_left"] == len(mc.sessions_between("2026-11-12", "2026-12-02"))


def test_veille(world, cfg):
    llm = FakeLLM(news=NEWS)
    entry = run_veille(today="2026-11-14", run_at=RUN_AT, cfg=cfg, ledger=world["ledger"], store=world["store"],
                       journal=world["journal"], news_items=[], llm=llm)
    assert entry["mode"] == "veille" and world["journal"].day_path("2026-11-14", "veille").exists()
    assert [c[0] for c in llm.calls] == ["news"]


# --- Allers-retours et post-mortems ------------------------------------------

def fill(d, side, qty, price, fees="1.00", t="AAA.PA"):
    return Fill(d, t, side, qty, D(price), D(price), D(fees), D(price) * qty)


def test_round_trips():
    fills = [fill("2026-11-03", BUY, 10, 10), fill("2026-11-04", BUY, 10, 12), fill("2026-11-05", SELL, 5, 13),
             fill("2026-11-06", SELL, 15, 11), fill("2026-11-09", BUY, 3, 10)]
    trips = round_trips(fills)
    closed, open_ = trips
    assert closed["closed"] and closed["trade_id"] == "AAA.PA-2026-11-03" and closed["close_date"] == "2026-11-06"
    assert closed["avg_buy"] == D("11") and closed["pnl"] == D("230") - D("220") - D("4")
    assert not open_["closed"] and open_["held"] == 3


def test_postmortems(world, cfg):
    led, j = world["ledger"], world["journal"]
    led.record_fill(fill("2026-11-04", BUY, 10, 10))
    led.record_fill(fill("2026-11-10", SELL, 10, 11))
    j.write_day({"date": "2026-11-03", "mode": "trading", "run_at": "x",
                 "final_orders": [{"ticker": "AAA.PA", "side": BUY, "qty": 10, "thesis": "rebond attendu",
                                   "exit_plan": "+10 %"}]})
    pm = {"postmortems": [{"trade_id": "AAA.PA-2026-11-04", "expected": "e", "happened": "h", "lesson": "l"}]}
    llm = FakeLLM(postmortem=pm)
    items = run_postmortems(as_of=AS_OF, cfg=cfg, ledger=led, store=world["store"], journal=j, llm=llm)
    assert items[0]["pnl"] == "8.00" and items[0]["lesson"] == "l"
    assert "rebond attendu" in llm.calls[0][1]
    assert run_postmortems(as_of=AS_OF, cfg=cfg, ledger=led, store=world["store"], journal=j, llm=llm) == []
    assert len(llm.calls) == 1                                   # pas de doublon


def test_digest():
    e = {"date": "2026-11-11", "executions": [{"side": BUY, "ticker": "A", "filled": False, "reason": "cash"}],
         "chef": {"market_view": "vue", "no_trade_reason": ""},
         "final_orders": [{"side": BUY, "qty": 3, "ticker": "B", "thesis": "t"}],
         "refused": [{"by": "risque", "side": BUY, "ticker": "C", "reason": "trop risqué"}],
         "portfolio": {"total": "1001.00", "return_pct": "0.1"}}
    d = digest(e)
    assert "NON exécuté : cash" in d and "BUY 3 B" in d and "Refusé (risque)" in d
