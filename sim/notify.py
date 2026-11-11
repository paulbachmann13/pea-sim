"""Notifications push sur téléphone via ntfy (application gratuite Android / iOS).

Publication en JSON (et non via les en-têtes HTTP) pour que les accents passent correctement.
"""
import logging

import requests

log = logging.getLogger(__name__)


def notify(cfg: dict, title: str, message: str, priority: int = 3, tags=()) -> bool:
    n = cfg["notify"]
    if not n.get("ntfy_topic"):
        log.warning("notification non envoyée (ntfy_topic vide) : %s", title)
        return False
    payload = {"topic": n["ntfy_topic"], "title": title, "message": message[:3500],
               "priority": priority, "tags": list(tags)}
    if n.get("dashboard_url"):
        payload["click"] = n["dashboard_url"]
    try:
        requests.post(n["ntfy_server"], json=payload, timeout=20).raise_for_status()
        return True
    except Exception:
        log.exception("échec de l'envoi de la notification")
        return False
