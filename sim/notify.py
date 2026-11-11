"""Alertes : e-mail (SMTP, ex. iCloud avec un mot de passe d'application) et/ou push ntfy.

Chaque canal est optionnel ; une alerte est considérée envoyée si au moins un canal a fonctionné.
Les identifiants vivent dans local.yaml (non versionné).
"""
import logging
import smtplib
from email.message import EmailMessage

import requests

log = logging.getLogger(__name__)


def send_email(n: dict, title: str, message: str) -> bool:
    if not (n.get("email") and n.get("smtp_password")):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = title, n.get("smtp_user") or n["email"], n["email"]
    body = message + (f"\n\nDashboard : {n['dashboard_url']}" if n.get("dashboard_url") else "")
    msg.set_content(body)
    try:
        with smtplib.SMTP(n["smtp_host"], n["smtp_port"], timeout=30) as s:
            s.starttls()
            s.login(n.get("smtp_user") or n["email"], n["smtp_password"])
            s.send_message(msg)
        return True
    except Exception:
        log.exception("échec de l'envoi du mail")
        return False


def send_ntfy(n: dict, title: str, message: str, priority: int, tags) -> bool:
    if not n.get("ntfy_topic"):
        return False
    payload = {"topic": n["ntfy_topic"], "title": title, "message": message[:3500],
               "priority": priority, "tags": list(tags)}
    if n.get("dashboard_url"):
        payload["click"] = n["dashboard_url"]
    try:
        requests.post(n["ntfy_server"], json=payload, timeout=20).raise_for_status()
        return True
    except Exception:
        log.exception("échec de l'envoi ntfy")
        return False


def notify(cfg: dict, title: str, message: str, priority: int = 3, tags=()) -> bool:
    n = cfg["notify"]
    sent = [send_email(n, title, message), send_ntfy(n, title, message, priority, tags)]
    if not any(sent):
        log.warning("alerte non envoyée (aucun canal configuré ou fonctionnel) : %s", title)
    return any(sent)
