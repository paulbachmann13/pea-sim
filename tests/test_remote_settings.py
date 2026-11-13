import pytest

from sim.remote_settings import apply_remote_settings


@pytest.fixture
def rcfg(cfg):
    cfg["supabase"]["enabled"] = True
    return cfg


def test_pwa_choice_overrides_local_mode(rcfg, tmp_path):
    msg = apply_remote_settings(rcfg, tmp_path, lambda c: {"mode": "eco", "pause": False})
    b = rcfg["budget"]
    assert (b["mode"], b["daily_usd"], b["weekly_usd"], b["pause"]) == ("eco", 0.8, 5.0, False)
    assert "mode eco" in msg


def test_pause_from_the_pwa(rcfg, tmp_path):
    apply_remote_settings(rcfg, tmp_path, lambda c: {"mode": "large", "pause": True})
    assert rcfg["budget"]["pause"] is True and rcfg["budget"]["daily_usd"] == 2.5


def test_unknown_mode_is_ignored_and_no_setting_changes_nothing(rcfg, tmp_path):
    apply_remote_settings(rcfg, tmp_path, lambda c: {"mode": "turbo", "pause": False})
    assert rcfg["budget"]["mode"] == "normal"
    assert apply_remote_settings(rcfg, tmp_path / "vide", lambda c: None) is None
    assert rcfg["budget"]["mode"] == "normal"


def test_last_known_choice_is_kept_when_offline(rcfg, tmp_path):
    apply_remote_settings(rcfg, tmp_path, lambda c: {"mode": "eco", "pause": False})
    rcfg["budget"]["mode"] = "normal"

    def offline(c):
        raise ConnectionError("pas de réseau")
    apply_remote_settings(rcfg, tmp_path, offline)
    assert rcfg["budget"]["mode"] == "eco"                           # le cache du dernier choix


def test_disabled_supabase_is_a_noop(cfg, tmp_path):
    assert apply_remote_settings(cfg, tmp_path, lambda c: {"mode": "eco"}) is None and cfg["budget"]["mode"] == "normal"


def test_run_applies_the_pwa_setting_first(tmp_path, rcfg):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from sim import daily
    rcfg["supabase"]["sync_token"] = ""                              # pas de synchro d'écriture dans ce test
    rcfg["publish"]["enabled"] = False
    (tmp_path / "data").mkdir()
    daily.run(datetime(2026, 11, 21, 10, tzinfo=ZoneInfo("Europe/Paris")), rcfg, tmp_path,
              fetch_settings=lambda c: {"mode": "eco", "pause": True}, fetch=lambda c, w: ([], []),
              llm=lambda *a, **k: (_ for _ in ()).throw(AssertionError("aucun appel LLM attendu")))
    assert rcfg["budget"]["mode"] == "eco" and rcfg["budget"]["pause"] is True
