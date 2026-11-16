import json
import re

import pytest

from sim.export import export_profiles
from sim.knowledge import Knowledge
from sim.llm import BudgetSkip
from sim.profiles import ETF_PROFILES, basic_profile, etf_profiles, refresh_profiles
from test_agents_decide import MARKET, NEWS, NEWS_ITEM, RUN_AT, FakeLLM, chef_with, risk_all, world  # noqa: F401
from sim.decide import run_decision

DAY = "2026-11-16"
INFO = {
    "AAA.PA": {"name": "AAA SA", "sector": "Healthcare", "industry": "Biotechnology", "country": "France",
               "summary": "AAA is a clinical-stage biotech company developing treatments for diabetes."},
    "BBB.PA": {"name": "BBB SE", "sector": "Energy", "industry": "Oil & Gas", "country": "France", "summary": "Oil major."},
}


def written(tickers):
    return {"profiles": [{"ticker": t, "domain": "Biotechnologie", "short": f"Biotech française : {t}",
                          "long": "Elle développe des traitements du diabète. Elle n'a pas encore de produit sur le marché."}
                         for t in tickers]}


@pytest.fixture
def kb(tmp_path):
    return Knowledge(tmp_path / "knowledge")


def test_every_whitelisted_etf_has_a_plain_french_profile(cfg):
    assert set(ETF_PROFILES) == set(cfg["eligibility"]["etf_whitelist"])
    for t, p in etf_profiles().items():
        assert p["kind"] == "ETF" and p["short"] and "panier" in p["long"] and p["name"]
    assert "MSCI World" in ETF_PROFILES["WPEA.PA"]["long"] and "S&P 500" in ETF_PROFILES["ESE.PA"]["long"]


def test_basic_profile_translates_sector_and_country():
    p = basic_profile("AAA.PA", INFO["AAA.PA"])
    assert p["domain"] == "Santé" and p["country"] == "France" and p["kind"] == "Action"
    assert p["short"] == "AAA SA — Santé (France)" and p["long"] == ""


def test_refresh_builds_basic_then_llm_profiles_and_keeps_them(cfg, kb):
    llm = FakeLLM(profile=lambda prompt: written(["AAA.PA", "BBB.PA"]))
    built, metas = refresh_profiles(cfg, kb, ["AAA.PA", "WPEA.PA", "BBB.PA"], DAY, llm=llm, fetch=INFO.__getitem__)
    assert built == 2 and len(metas) == 1                                  # l'ETF n'est pas traité (fiche à la main)
    p = kb.profiles()["AAA.PA"]
    assert p["short"] == "Biotech française : AAA.PA" and p["long"].startswith("Elle développe") and p["domain"] == "Biotechnologie"
    prompt = llm.calls[0][1]
    assert "diabetes" in prompt and "Healthcare / Biotechnology" in prompt and "WPEA" not in prompt   # faits réels fournis
    assert llm.tools["profile"] is None                                    # aucun accès web pour cet agent
    again, metas = refresh_profiles(cfg, kb, ["AAA.PA", "BBB.PA"], DAY, llm=llm, fetch=INFO.__getitem__)
    assert (again, metas) == (0, []) and len(llm.calls) == 1                # déjà faites : aucun nouvel appel


def test_basic_profiles_survive_a_budget_skip_or_a_failed_lookup(cfg, kb, monkeypatch):
    def flaky(t):
        if t == "BBB.PA":
            raise ConnectionError("yfinance en panne")
        return INFO[t]
    monkeypatch.setattr("sim.profiles.agents.profile_writer",
                        lambda *a, **k: (_ for _ in ()).throw(BudgetSkip("profile", "budget du jour atteint")))
    with pytest.raises(BudgetSkip):
        refresh_profiles(cfg, kb, ["AAA.PA", "BBB.PA"], DAY, fetch=flaky)
    profs = kb.profiles()
    assert "AAA.PA" in profs and profs["AAA.PA"]["long"] == "" and "BBB.PA" not in profs   # BBB sera retenté plus tard


def test_llm_answer_is_checked(cfg, kb):
    bad = {"profiles": [{"ticker": "AAA.PA", "domain": "x", "short": "y" * 200, "long": "ok"}]}
    good = written(["AAA.PA"])
    answers = iter([bad, good])
    llm = FakeLLM(profile=lambda p: next(answers))
    refresh_profiles(cfg, kb, ["AAA.PA"], DAY, llm=llm, fetch=INFO.__getitem__)
    assert len(llm.calls) == 2 and "trop longue" in llm.calls[1][1]          # refusé puis corrigé
    assert kb.profiles()["AAA.PA"]["short"].startswith("Biotech")


def test_export_merges_stocks_and_etfs_without_source_data(cfg, kb, tmp_path):
    kb.set_profile("AAA.PA", basic_profile("AAA.PA", INFO["AAA.PA"]), DAY)
    export_profiles(cfg, tmp_path, tmp_path / "out")
    out = json.loads((tmp_path / "out" / "profiles.json").read_text(encoding="utf-8"))
    assert out["AAA.PA"]["short"].startswith("AAA SA") and "source" not in out["AAA.PA"]
    assert out["WPEA.PA"]["kind"] == "ETF" and len(out) == 1 + len(ETF_PROFILES)


def test_decision_builds_profiles_for_held_ordered_and_watched_titles(cfg, world):
    llm = FakeLLM(market=MARKET, news=NEWS, chef=chef_with(("AAA.PA", "BUY", 5)), risk=risk_all(),
                  profile=lambda p: written(re.findall(r"^### (\S+)", p, re.M)))     # répond pour les titres demandés
    entry = run_decision(as_of="2026-11-11", run_at=RUN_AT, cfg=cfg, llm=llm, news_items=[NEWS_ITEM],
                         profile_fetch=lambda t: INFO.get(t, {"name": t, "sector": "", "industry": "", "country": "", "summary": ""}),
                         fetch_hist=lambda c, n, a: [], watch={"BBB.PA": "BBB"}, **world)
    assert entry["errors"] == []
    assert entry["learning"]["profiles_built"] >= 2
    assert {"AAA.PA", "BBB.PA"} <= set(world["kb"].profiles())
    assert "profile" in [r for r, _ in llm.calls]
