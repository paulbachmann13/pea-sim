import json

from sim.export import agent_states, export_agents
from sim.journal import write_json


def call(role, model="sonnet", s=10):
    return {"role": role, "model": model, "duration_s": s, "input_tokens": 1000, "output_tokens": 200}


def day(d, calls, errors=(), mode="trading", **kw):
    return {"date": d, "mode": mode, "run_at": f"{d}T18:00:00+01:00", "llm_calls": calls, "errors": list(errors),
            "portfolio": {}, "screener": {}, **kw}


def test_states_ok_skipped_error():
    e = day("2026-11-13", [call("market"), call("chef", "opus", 30)], ["analyste actualité : quota"])
    s = agent_states(e)
    assert [s[k]["status"] for k in ("market", "news", "chef", "risk", "review")] == \
           ["ok", "error", "ok", "skipped", "skipped"]
    assert s["chef"]["model"] == "opus" and s["chef"]["seconds"] == 30 and s["chef"]["tokens_in"] == 1000
    assert "quota" in s["news"]["error"] and s["ledger"]["status"] == "ok"


def test_postmortem_and_run_warnings_and_veille():
    e = day("2026-11-13", [call("chef")], learning={"postmortems": ["AAA-2026-11-04"]})
    assert agent_states(e)["postmortem"]["status"] == "ok"
    assert agent_states(e, ["post-mortems : boom"])["postmortem"]["status"] == "error"
    v = day("2026-11-14", [call("news"), call("weekly", "opus")], mode="veille")
    s = agent_states(v)
    assert s["weekly"]["status"] == "ok" and s["chef"]["status"] == "skipped" and s["ledger"]["status"] == "skipped"
    assert agent_states(day("2026-12-16", [], final=True))["report"]["status"] == "ok"


def test_export_agents_last_run_and_history(tmp_path, cfg):
    j = tmp_path / "journal"
    for i in range(9):
        write_json(j / f"2026-11-{10 + i}.json", day(f"2026-11-{10 + i}", [call("chef")]))
    write_json(j / "2026-11-19-veille.json", day("2026-11-19", [call("news")], mode="veille"))
    export_agents(cfg, tmp_path)
    read = lambda: json.loads((tmp_path / "docs" / "data" / "agents.json").read_text(encoding="utf-8"))
    out = read()
    assert out["last_run"]["mode"] == "veille" and len(out["history"]) == 7
    by_key = {a["key"]: a for a in out["agents"]}
    assert len(out["agents"]) == 12 and sum(a["kind"] == "llm" for a in out["agents"]) == 9
    assert by_key["news"]["status"] == "ok" and by_key["chef"]["status"] == "skipped"
    assert by_key["market"]["model"] == cfg["agents"]["models"]["market"]       # modèle configuré si pas d'appel
    export_agents(cfg, tmp_path, ["revue hebdomadaire : quota"])                # avertissement du run en cours
    out = read()
    assert {a["key"]: a for a in out["agents"]}["weekly"]["status"] == "error"
    assert out["history"][-1]["states"]["weekly"] == "error" and out["history"][0]["states"]["weekly"] == "skipped"
