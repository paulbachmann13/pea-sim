import pandas as pd
import pytest

from sim import market_calendar as mc
from sim.data import PriceStore
from sim.dossier import fetch_history
from sim.knowledge import Knowledge, confidence
from sim.news import NewsItem, format_news, price_reactions

DAY = "2026-11-12"


@pytest.fixture
def kb(tmp_path):
    return Knowledge(tmp_path / "knowledge")


def add(text="Si X, alors éviter Y", origin="T1"):
    return {"op": "add", "rule_id": "", "text": text, "scope": "chef", "origin": origin, "reason": "r"}


def vote(kind, origin, rid="R1"):
    return {"op": kind, "rule_id": rid, "text": "", "scope": "chef", "origin": origin, "reason": "r"}


# --- Playbook et garde-fous -----------------------------------------------------

def test_new_rule_is_always_hypothesis(kb, cfg):
    changes = kb.apply_playbook_ops([add()], DAY, "post-mortem", cfg)
    r = kb.active_rules()[0]
    assert r["id"] == "R1" and r["confidence"] == "hypothèse" and changes[0]["op"] == "ajout"
    assert (kb.root / f"playbook/{DAY}.json").exists()                     # version datée
    assert kb.apply_playbook_ops([add()], DAY, "x", cfg) == []              # doublon ignoré


def test_confidence_needs_distinct_cases(kb, cfg):
    kb.apply_playbook_ops([add(origin="T1")], DAY, "pm", cfg)
    kb.apply_playbook_ops([vote("support", "T1")], DAY, "revue", cfg)      # même cas : ne compte pas
    assert kb.active_rules()[0]["confidence"] == "hypothèse"
    kb.apply_playbook_ops([vote("support", "T2")], DAY, "revue", cfg)
    assert kb.active_rules()[0]["confidence"] == "probable"
    kb.apply_playbook_ops([vote("support", "T3"), vote("support", "T4")], DAY, "revue", cfg)
    assert kb.active_rules()[0]["confidence"] == "confirmée"


def test_contradicted_rule_is_contested_then_retired(kb, cfg):
    kb.apply_playbook_ops([add(origin="T1")], DAY, "pm", cfg)
    kb.apply_playbook_ops([vote("contradict", "T2")], DAY, "revue", cfg)
    assert kb.active_rules()[0]["confidence"] == "contestée"
    assert "CONTESTÉE" in kb.render_playbook()
    changes = kb.apply_playbook_ops([vote("contradict", "T3"), vote("contradict", "T4"), vote("support", "T9")],
                                    DAY, "revue", cfg)                     # 1 pour, 3 contre
    assert "retirée automatiquement" in changes[1]["op"] and kb.active_rules() == []
    assert len(changes) == 2                                                # plus de vote sur une règle retirée
    assert len(kb.playbook()["rules"][0]["history"]) == 4                  # historique conservé


def test_revise_and_retire_are_logged(kb, cfg):
    kb.apply_playbook_ops([add()], DAY, "pm", cfg)
    kb.apply_playbook_ops([{**vote("revise", "T2"), "text": "Si X et Z, alors éviter Y"}], "2026-11-13", "hebdo", cfg)
    assert kb.active_rules()[0]["text"] == "Si X et Z, alors éviter Y"
    kb.apply_playbook_ops([vote("retire", "T3")], "2026-11-14", "hebdo", cfg)
    log = (kb.root / "playbook_log.json").read_text(encoding="utf-8")
    assert "révisée" in log and "retirée" in log and kb.active_rules() == []


def test_daily_cap_on_new_rules(kb, cfg):
    ops = [add(f"Si cas {i}, alors Y", f"T{i}") for i in range(5)]
    changes = kb.apply_playbook_ops(ops, DAY, "revue", cfg)
    assert len(kb.active_rules()) == cfg["knowledge"]["max_new_rules_per_day"]
    assert changes[-1]["op"] == "refusée"


def test_confidence_function():
    r = lambda s, c: {"evidence": [{"kind": "support"}] * s + [{"kind": "contradict"}] * c}
    assert [confidence(r(1, 0)), confidence(r(2, 0)), confidence(r(4, 1)), confidence(r(2, 2))] == \
           ["hypothèse", "probable", "confirmée", "contestée"]


# --- Prédictions et calibration ----------------------------------------------------

DAYS = mc.sessions_between("2026-10-01", "2026-11-20")


@pytest.fixture
def store():
    s = PriceStore(":memory:")
    for t, start, step in [("AAA.PA", 10, 0.1), ("BBB.PA", 10, -0.1), ("WPEA.PA", 7, 0.0)]:
        closes = [start + step * i for i in range(len(DAYS))]
        s.upsert_bars(t, pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                                       "adj_close": closes, "volume": [1000] * len(DAYS)}, index=DAYS), "t")
    return s


def pred(ticker, direction, prob, rules=("R1",)):
    return {"date": "2026-11-10", "ticker": ticker, "kind": "ordre BUY", "thesis": "t", "direction": direction,
            "move_pct": 1, "horizon_sessions": 2, "probability": prob, "target_date": "2026-11-12",
            "ref_price": 0, "rules": list(rules), "catalysts": []}


def test_predictions_resolved_at_target_without_future(kb, store):
    kb.add_predictions([pred("AAA.PA", "up", 0.8), pred("BBB.PA", "up", 0.8), pred("AAA.PA", "down", 0.6, ())])
    assert kb.resolve_predictions(store, "2026-11-11", "WPEA.PA") == []     # pas encore échue
    res = kb.resolve_predictions(store, "2026-11-12", "WPEA.PA")
    assert [p["hit"] for p in res] == [True, False, False] and res[0]["bench_pct"] == 0.0
    c = kb.calibration()
    assert c["n"] == 3 and c["announced"] == 73 and c["observed"] == 33
    assert "SURCONFIANT" in kb.render_calibration()
    eff = kb.rule_effects()
    assert set(eff) == {"R1"} and eff["R1"]["n"] == 2 and eff["R1"]["hits"] == 1
    assert eff["R1"]["avg_return"] == round((res[0]["return_pct"] + res[1]["return_pct"]) / 2, 2)
    assert "appliquée 2 fois, 1/2" in (kb.apply_playbook_ops([add()], DAY, "x", {"knowledge": {
        "max_new_rules_per_day": 3, "max_active_rules": 40}}) and kb.render_playbook())


# --- Notes, connaissances, agenda ----------------------------------------------------

def test_notes_keep_versions(kb):
    kb.update_notes([{"key": "entreprise:AAA.PA", "text": "v1"}, {"key": "bidon", "text": "x"}], "2026-11-10", "a")
    kb.update_notes([{"key": "entreprise:AAA.PA", "text": "v2"}], DAY, "a")
    n = kb.notes()["entreprise:AAA.PA"]
    assert n["text"] == "v2" and n["versions"][-1]["text"] == "v1" and "bidon" not in kb.notes()
    kb.set_dossier("AAA.PA", "AAA", {"sector": "Énergie", "summary": "s", "outlook": "o",
                                      "timeline": [{"date": "2026-07-01", "kind": "résultats", "event": "S1 record"}]}, DAY)
    assert kb.has_dossier("AAA.PA") and "S1 record" in kb.render_notes(["AAA.PA"])
    assert n["text"] in kb.render_notes(["AAA.PA"])


def test_insights_dedup_and_obsolete(kb):
    item = {"kind": "biais", "subject": "chef", "text": "Achète trop après les hausses."}
    assert kb.add_insights([item, item], DAY, "hebdo") == 1
    kb.add_insights([], DAY, "hebdo", obsolete_ids={"I1"})
    assert kb.render_insights() == "(aucune pour l'instant)"


def test_agenda_merges_reworded_events(kb):
    kb.update_agenda([{"date": "2026-12-01", "event": "Réunion Fed de décembre (date exacte non fournie)",
                       "tickers": [], "impact": "taux"}], DAY, "news")
    kb.update_agenda([{"date": "2026-12-01", "event": "Réunion de la Fed en décembre (date à vérifier)",
                       "tickers": [], "impact": ""},
                      {"date": "", "event": "Réunion de la Fed en décembre", "tickers": [], "impact": ""},
                      {"date": "2026-11-20", "event": "Résultats AAA", "tickers": [], "impact": ""}], DAY, "chef")
    assert [e["event"] for e in kb.agenda()] == ["Réunion Fed de décembre (date exacte non fournie)", "Résultats AAA"]


def test_agenda_hides_past_and_keeps_scenarios(kb):
    kb.update_agenda([{"date": "2026-11-20", "event": "Résultats AAA", "tickers": ["AAA.PA"], "impact": "fort"},
                      {"date": "2026-11-01", "event": "Passé", "tickers": [], "impact": ""}], DAY, "news")
    kb.update_agenda([{"event": "Résultats AAA", "date": "2026-11-20", "if": "résultats > attentes",
                       "then": "renforcer"}], DAY, "chef")
    txt = kb.render_agenda(DAY)
    assert "Résultats AAA" in txt and "si résultats > attentes → renforcer" in txt and "Passé" not in txt


# --- « Déjà dans le prix » et historique sans information future --------------------

def test_price_reaction_since_publication(store):
    items = [NewsItem("n1", "s", "AAA.PA", "contrat", "", "2026-11-10T08:00:00+00:00", ""),
             NewsItem("n2", "s", "", "macro", "", "2026-11-10T08:00:00+00:00", "")]
    r = price_reactions(items, store, "2026-11-12")
    before, now = store.last_close("AAA.PA", "2026-11-09"), store.last_close("AAA.PA", "2026-11-12")
    assert r == {"n1": round((float(now) / float(before) - 1) * 100, 2)}
    assert "[cours depuis publication : +" in format_news(items, r)


def test_history_excludes_future(cfg, monkeypatch):
    rss = """<?xml version="1.0"?><rss><channel>
      <item><title>Ancien</title><link>a</link><pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
      <item><title>Du jour</title><link>b</link><pubDate>Thu, 12 Nov 2026 09:00:00 GMT</pubDate></item>
      <item><title>Futur</title><link>c</link><pubDate>Fri, 13 Nov 2026 09:00:00 GMT</pubDate></item>
    </channel></rss>""".encode()

    class R:
        content = rss

        def raise_for_status(self):
            pass
    monkeypatch.setattr("sim.dossier.requests.get", lambda *a, **k: R())
    items = fetch_history(cfg, "AAA", DAY)
    assert [i.title for i in items] == ["Ancien"]                          # strictement avant la décision
