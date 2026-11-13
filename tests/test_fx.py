import json
from datetime import date

from sim import export
from sim.fx import FALLBACK, usd_to_eur

D1, D2 = date(2026, 11, 13), date(2026, 11, 14)


def test_rate_is_fetched_once_per_day_and_cached(tmp_path):
    calls = []

    def fetch():
        calls.append(1)
        return 0.9048, "2026-11-13"
    (tmp_path / "data").mkdir()
    a = usd_to_eur(tmp_path, D1, fetch)
    assert a == {"rate": 0.9048, "date": "2026-11-13", "source": "BCE", "fetched": "2026-11-13"}
    assert usd_to_eur(tmp_path, D1, fetch)["rate"] == 0.9048 and len(calls) == 1      # cache du jour
    usd_to_eur(tmp_path, D2, fetch)
    assert len(calls) == 2                                                             # nouveau jour : nouvelle requête


def test_offline_keeps_last_known_rate_then_falls_back(tmp_path):
    (tmp_path / "data").mkdir()

    def down():
        raise ConnectionError("pas de réseau")
    first = usd_to_eur(tmp_path, D1, down)
    assert first["rate"] == FALLBACK and first["date"] is None and first["source"] == "taux approximatif"
    usd_to_eur(tmp_path, D1, lambda: (0.88, "2026-11-13"))
    later = usd_to_eur(tmp_path, D2, down)
    assert later["rate"] == 0.88 and later["source"] == "dernier taux connu"


def test_usage_export_carries_the_rate(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(export, "usd_to_eur", lambda root: {"rate": 0.9, "date": "2026-11-13", "source": "BCE"})
    export.export_usage(cfg, tmp_path)
    out = json.loads((tmp_path / "docs" / "data" / "usage.json").read_text(encoding="utf-8"))
    assert out["eur"]["rate"] == 0.9 and out["mode"] == "normal" and "today" in out
