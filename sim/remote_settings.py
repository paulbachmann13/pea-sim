"""Réglages choisis depuis la PWA (mode du budget, pause), lus au début de chaque run.

La PWA écrit dans la table Supabase `pea_settings` via une fonction protégée par un code. Le run les relit (lecture
publique) et les applique par-dessus config.yaml / local.yaml : le choix fait sur le téléphone l'emporte.
Dernier réglage connu mis en cache (data/remote_settings.json) : sans réseau, on garde le dernier choix.
"""
import logging
from pathlib import Path

import requests

from .config import apply_budget_mode
from .journal import read_json, write_json

log = logging.getLogger(__name__)


def fetch_remote_settings(cfg: dict) -> dict | None:
    s = cfg["supabase"]
    r = requests.get(f"{s['url']}/rest/v1/pea_settings?select=data&key=eq.budget",
                     headers={"apikey": s["publishable_key"]}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    return rows[0]["data"] if rows else None


def apply_remote_settings(cfg: dict, root: Path, fetch=fetch_remote_settings) -> str | None:
    """Applique le réglage de la PWA à `cfg` (en place). Renvoie une phrase décrivant ce qui a été appliqué."""
    if not cfg["supabase"].get("enabled"):
        return None
    cache = root / "data" / "remote_settings.json"
    try:
        data = fetch(cfg)
        if data is not None:
            write_json(cache, data)
    except Exception:
        log.warning("réglages de la PWA illisibles : dernier choix connu conservé")
        data = read_json(cache, None)
    if not data:
        return None
    b = cfg["budget"]
    if data.get("mode") in b["presets"]:
        b["mode"] = data["mode"]
        apply_budget_mode(cfg)
    b["pause"] = bool(data.get("pause", b.get("pause", False)))
    return f"réglage PWA : mode {b['mode']}{', en pause' if b['pause'] else ''}"
