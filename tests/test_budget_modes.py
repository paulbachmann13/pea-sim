import pytest

from sim.config import ConfigError, load_config
from sim.usage import Usage, summarize
from test_usage import MON, clock, spend


def write_local(tmp_path, text):
    p = tmp_path / "local.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_default_mode_is_normal():
    b = load_config(local=None)["budget"]
    assert b["mode"] == "normal" and (b["daily_usd"], b["weekend_usd"], b["weekly_usd"], b["tier3_share"]) == (1.2, 3.0, 8.0, 0.6)


@pytest.mark.parametrize("mode, daily, weekly", [("eco", 0.8, 5.0), ("large", 2.5, 15.0)])
def test_mode_chosen_in_local_yaml(tmp_path, mode, daily, weekly):
    b = load_config(local=write_local(tmp_path, f"budget:\n  mode: {mode}\n"))["budget"]
    assert (b["mode"], b["daily_usd"], b["weekly_usd"]) == (mode, daily, weekly)


def test_overrides_apply_on_top_of_the_mode(tmp_path):
    b = load_config(local=write_local(tmp_path, "budget:\n  mode: eco\n  overrides: {weekly_usd: 6, tier3_share: 0.3}\n"))["budget"]
    assert (b["daily_usd"], b["weekly_usd"], b["tier3_share"]) == (0.8, 6, 0.3)


def test_unknown_mode_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="budget.mode inconnu"):
        load_config(local=write_local(tmp_path, "budget:\n  mode: turbo\n"))


def test_summary_gives_typical_day_tokens_per_dollar_and_modes(tmp_path, cfg):
    u = Usage(tmp_path / "u.jsonl", clock=clock(MON))
    for _ in range(3):                                                    # un jour de bourse « complet » : 3 appels
        u.record(role="chef", model="opus", ok=True, tokens_in=30000, tokens_out=4000, usd=0.2, seconds=5)
    s = summarize(cfg, u)
    assert s["typical_day_usd"] == 0.6 and s["days_measured"] == 1
    assert s["per_usd"] == {"in": 150000, "out": 20000}                  # 90 000 lus et 12 000 écrits pour 0,6 $
    assert s["mode"] == "normal" and set(s["presets"]) == {"eco", "normal", "large"}
    assert s["presets"]["eco"]["weekly_usd"] == 5.0


def test_summary_without_history_has_sane_defaults(tmp_path, cfg):
    s = summarize(cfg, Usage(tmp_path / "none.jsonl", clock=clock(MON)))
    assert s["typical_day_usd"] is None and s["per_usd"]["in"] == 180000 and s["total"]["calls"] == 0
