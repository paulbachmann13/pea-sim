import json
from datetime import datetime

import pytest

from sim.decide import run_decision
from sim.export import agent_states
from sim.llm import BudgetSkip, LLMError, QuotaError, ask
from sim.usage import PARIS, Usage, check, quota_reset, summarize
from test_agents_decide import AS_OF, MARKET, NEWS, NEWS_ITEM, RUN_AT, FakeLLM, chef_with, risk_all, world  # noqa: F401

MON, SAT = "2026-11-16", "2026-11-21"


def clock(day):
    return lambda: datetime.fromisoformat(day).replace(hour=18, tzinfo=PARIS)


@pytest.fixture
def bcfg(cfg):
    cfg["budget"].update(enabled=True, daily_usd=1.0, weekend_usd=3.0, weekly_usd=5.0, tier3_share=0.5, pause=False)
    return cfg


def spend(u, usd, role="chef", day=None):
    u.record(role=role, model="opus", ok=True, tokens_in=1000, tokens_out=100, usd=usd, seconds=5)


# --- Budget et priorités ----------------------------------------------------------

def test_tiers_degrade_as_budget_is_used(tmp_path, bcfg):
    u = Usage(tmp_path / "u.jsonl", clock=clock(MON))
    assert check("dossier", bcfg, u)[0] and check("news", bcfg, u)[0]
    spend(u, 0.55)                                               # > 50 % du budget du jour
    assert not check("dossier", bcfg, u)[0] and not check("research", bcfg, u)[0]   # optionnel : coupé
    assert check("news", bcfg, u)[0] and check("review", bcfg, u)[0]                # cœur : encore OK
    spend(u, 0.50)                                               # budget du jour dépassé
    ok, why = check("news", bcfg, u)
    assert not ok and "budget du jour" in why
    assert check("chef", bcfg, u)[0] and check("risk", bcfg, u)[0] and check("report", bcfg, u)[0]   # essentiel


def test_weekend_and_weekly_caps(tmp_path, bcfg):
    sat = Usage(tmp_path / "u.jsonl", clock=clock(SAT))
    spend(sat, 2.0)                                              # 2 $ : sous les 3 $ du week-end
    assert check("news", bcfg, sat)[0]
    spend(sat, 3.2)                                              # 5,2 $ sur la semaine ISO (plafond 5 $)
    ok, why = check("news", bcfg, sat)
    assert not ok and "budget" in why


def test_pause_and_disabled(tmp_path, bcfg):
    u = Usage(tmp_path / "u.jsonl", clock=clock(MON))
    bcfg["budget"]["pause"] = True
    assert not check("chef", bcfg, u)[0]                         # la pause coupe tout, même l'essentiel
    bcfg["budget"]["enabled"] = False
    assert check("chef", bcfg, u)[0]


def test_pause_blocks_every_call_even_with_the_budget_disabled(cfg):
    cfg["budget"].update(enabled=False, pause=True)
    with pytest.raises(BudgetSkip, match="pause"):
        ask("chef", "P", "S", {}, cfg, llm=fake_llm)


def test_global_cap_counts_other_projects(tmp_path, bcfg):
    shared = tmp_path / "shared.jsonl"
    bcfg["budget"]["global_daily_usd"] = 2.0
    u = Usage(tmp_path / "u.jsonl", shared, clock=clock(MON))
    shared.write_text(json.dumps({"date": MON, "project": "autre", "usd": 1.9}) + "\nligne cassée\n", encoding="utf-8")
    spend(u, 0.2)                                                # 0,2 $ ici + 1,9 $ ailleurs : plafond global dépassé
    ok, why = check("news", bcfg, u)
    assert not ok and "global" in why
    assert u.spent(MON) == pytest.approx(0.2)                    # le plafond propre ne compte que ce projet


# --- ask : journalisation, économies, quota -----------------------------------------

def fake_llm(*a, **k):
    return {"ok": 1}, {"model": "sonnet", "duration_s": 2.5, "input_tokens": 1234, "output_tokens": 56,
                       "cost_usd_equiv": 0.0421}


def test_ask_logs_every_call_and_skips_when_over_budget(tmp_path, bcfg):
    u = Usage(tmp_path / "u.jsonl", clock=clock(MON))
    ask("news", "P", "S", {}, bcfg, llm=fake_llm, usage=u)
    row = u.rows()[0]
    assert (row["role"], row["tokens_in"], row["tokens_out"], row["usd"], row["ok"]) == ("news", 1234, 56, 0.0421, True)
    spend(u, 2.0)
    with pytest.raises(BudgetSkip) as e:
        ask("news", "P", "S", {}, bcfg, llm=fake_llm, usage=u)
    assert e.value.role == "news" and "budget du jour" in e.value.reason
    assert len(u.rows()) == 2                                    # l'appel refusé n'est pas passé
    assert ask("chef", "P", "S", {}, bcfg, llm=fake_llm, usage=u)[0] == {"ok": 1}


def test_quota_hit_is_logged_with_reset_time(tmp_path, bcfg):
    u = Usage(tmp_path / "u.jsonl", clock=clock(MON))

    def hit(*a, **k):
        raise QuotaError("quota", reset=quota_reset("You've hit your session limit · resets 6:30pm (Europe/Paris)"))
    with pytest.raises(LLMError, match="quota"):
        ask("chef", "P", "S", {}, bcfg, llm=hit, usage=u)
    assert u.quota_hits()[0]["reset"] == "6:30pm (Europe/Paris)"
    assert summarize(bcfg, u)["quota_hits"][0]["reset"].startswith("6:30pm")


def test_summarize(tmp_path, bcfg):
    u = Usage(tmp_path / "u.jsonl", clock=clock(MON))
    spend(u, 0.30, "chef")
    spend(u, 0.10, "news")
    s = summarize(bcfg, u)
    assert s["today"]["usd"] == 0.4 and s["today"]["cap"] == 1.0 and s["today"]["calls"] == 2
    assert s["today"]["by_role"] == {"chef": 0.3, "news": 0.1} and s["week"]["cap"] == 5.0
    assert s["days"][-1]["date"] == MON and s["total"]["tokens_in"] == 2000


# --- Intégration : le chef décide même quand le reste est économisé -------------------

def test_decision_still_happens_when_budget_is_exhausted(tmp_path, bcfg, world):
    bcfg["budget"].update(log=str(tmp_path / "usage.jsonl"), daily_usd=0.1, weekend_usd=0.1)
    Usage(tmp_path / "usage.jsonl").record(role="chef", ok=True, usd=5.0)         # budget (du vrai jour) dépassé
    llm = FakeLLM(market=MARKET, news=NEWS, chef=chef_with(("AAA.PA", "BUY", 5)), risk=risk_all())
    entry = run_decision(as_of=AS_OF, run_at=RUN_AT, cfg=bcfg, llm=llm, news_items=[NEWS_ITEM], **world)
    skipped = {s["role"] for s in entry["budget_skips"]}
    assert {"market", "news"} <= skipped and entry["errors"] == []              # économisés, pas des erreurs
    assert [r for r, _ in llm.calls] == ["chef", "risk"] and len(entry["final_orders"]) == 1
    states = agent_states(entry)
    assert states["market"]["status"] == "budget" and "budget du jour" in states["market"]["note"]
    assert states["chef"]["status"] == "ok"
