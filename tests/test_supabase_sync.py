import json

import pytest

from sim.supabase_sync import sync_docs


@pytest.fixture
def setup(tmp_path, cfg):
    cfg["supabase"].update(enabled=True, sync_token="tok")
    data = tmp_path / "docs" / "data"
    (data / "journal").mkdir(parents=True)
    (data / "summary.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    (data / "journal" / "2026-11-12.json").write_text(json.dumps({"d": 1}), encoding="utf-8")
    calls = []
    return cfg, data, tmp_path / "state.json", calls, lambda s, docs: calls.append(docs)


def test_sends_only_changes(setup):
    cfg, data, state, calls, post = setup
    assert sync_docs(cfg, data, state, post) == 2
    assert calls[0] == {"journal/2026-11-12.json": {"d": 1}, "summary.json": {"a": 1}}
    assert sync_docs(cfg, data, state, post) == 0
    (data / "summary.json").write_text(json.dumps({"a": 2}), encoding="utf-8")
    assert sync_docs(cfg, data, state, post) == 1
    assert calls[-1] == {"summary.json": {"a": 2}}


def test_failed_post_is_retried_next_time(setup):
    cfg, data, state, calls, post = setup

    def broken(s, docs):
        raise RuntimeError("down")
    with pytest.raises(RuntimeError):
        sync_docs(cfg, data, state, broken)
    assert sync_docs(cfg, data, state, post) == 2


def test_disabled_or_missing_token(setup):
    cfg, data, state, calls, post = setup
    cfg["supabase"]["enabled"] = False
    assert sync_docs(cfg, data, state, post) == 0
    cfg["supabase"].update(enabled=True, sync_token="")
    with pytest.raises(RuntimeError, match="sync_token"):
        sync_docs(cfg, data, state, post)
