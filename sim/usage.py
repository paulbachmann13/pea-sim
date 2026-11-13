"""Suivi de la consommation de l'abonnement Claude et budget de ce projet.

Chaque appel LLM est journalisé dans data/usage.jsonl (tokens, équivalent $ API, durée, échec/quota).
L'« équivalent $ API » sert d'unité de budget : il pondère les modèles (Opus coûte bien plus que Sonnet), donc
il reflète mieux la consommation de l'abonnement que les seuls tokens. Le quota réel restant n'est pas lisible
depuis le CLI, et d'autres projets le partagent : ce budget plafonne la part de CE projet.

Priorités quand le budget se resserre (config budget.*) :
  1 essentiel  : chef, gestionnaire de risque, rapport final → toujours autorisés (sauf pause)
  2 cœur       : analystes, revue quotidienne, post-mortems, plan → tant que le budget du jour/de la semaine n'est pas atteint
  3 optionnel  : dossiers, recherche web, revue hebdomadaire → seulement tant que `tier3_share` du budget n'est pas consommé
Un fichier partagé optionnel (budget.shared_log, mêmes lignes JSON) permet aux autres projets de déclarer leur
consommation, pour appliquer un plafond global (budget.global_daily_usd).
"""
import json
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
TIERS = {"chef": 1, "risk": 1, "report": 1, "market": 2, "news": 2, "review": 2, "postmortem": 2, "plan": 2,
         "dossier": 3, "research": 3, "weekly": 3}
RESET_RE = re.compile(r"resets (\d{1,2}(?::\d{2})?\s?[ap]m(?: \([^)]*\))?)", re.I)


class Usage:
    def __init__(self, path, shared=None, clock=None):
        self.path = Path(path)
        self.shared = Path(shared) if shared else None
        self.clock = clock or (lambda: datetime.now(PARIS))

    def today(self) -> str:
        return self.clock().date().isoformat()

    def record(self, **fields) -> None:
        line = {"ts": self.clock().isoformat(timespec="seconds"), "date": self.today(), "project": "pea_sim", **fields}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def rows(self, own_only: bool = False) -> list[dict]:
        out = []
        for p in ([self.path] if own_only or not self.shared else [self.path, self.shared]):
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("date"):
                    out.append(r)
        return out

    def spent(self, day: str, own_only: bool = True) -> float:
        return sum(r.get("usd") or 0 for r in self.rows(own_only) if r["date"] == day)

    def spent_week(self, day: str) -> float:
        y, w, _ = date.fromisoformat(day).isocalendar()[:3]
        return sum(r.get("usd") or 0 for r in self.rows(True) if date.fromisoformat(r["date"]).isocalendar()[:2] == (y, w))

    def quota_hits(self, n: int = 5) -> list[dict]:
        return [r for r in self.rows(True) if r.get("quota")][-n:]


def day_cap(cfg: dict, day: str) -> float:
    b = cfg["budget"]
    return b["weekend_usd"] if date.fromisoformat(day).weekday() >= 5 else b["daily_usd"]


def check(role: str, cfg: dict, usage: Usage) -> tuple[bool, str]:
    """(autorisé ?, motif du refus). Les refus sont des économies voulues, pas des erreurs."""
    b = cfg["budget"]
    if not b["enabled"]:
        return True, ""
    if b.get("pause"):
        return False, "pause demandée (budget.pause)"
    tier, day = TIERS.get(role, 2), usage.today()
    if tier == 1:
        return True, ""
    cap, wcap = day_cap(cfg, day), b["weekly_usd"]
    spent, week = usage.spent(day), usage.spent_week(day)
    share = b["tier3_share"] if tier == 3 else 1.0
    if spent >= cap * share:
        return False, f"budget du jour atteint ({spent:.2f} $ sur {cap * share:.2f} $)"
    if week >= wcap * (0.8 if tier == 3 else 1.0):
        return False, f"budget de la semaine atteint ({week:.2f} $ sur {wcap * (0.8 if tier == 3 else 1.0):.2f} $)"
    g = b.get("global_daily_usd")
    if g and usage.shared and usage.spent(day, own_only=False) >= g:
        return False, f"plafond global du jour atteint ({usage.spent(day, own_only=False):.2f} $ sur {g:.2f} $)"
    return True, ""


def quota_reset(text: str) -> str | None:
    m = RESET_RE.search(text or "")
    return m.group(1) if m else None


def summarize(cfg: dict, usage: Usage, days: int = 14) -> dict:
    """Synthèse pour le dashboard : jour, semaine, 14 derniers jours, répartition par agent, quotas atteints."""
    rows, today = usage.rows(True), usage.today()
    b = cfg["budget"]

    def agg(rs):
        by = {}
        for r in rs:
            by[r.get("role", "?")] = by.get(r.get("role", "?"), 0) + (r.get("usd") or 0)
        return {"usd": round(sum(r.get("usd") or 0 for r in rs), 3), "tokens_in": sum(r.get("tokens_in") or 0 for r in rs),
                "tokens_out": sum(r.get("tokens_out") or 0 for r in rs), "calls": sum(1 for r in rs if r.get("ok")),
                "failed": sum(1 for r in rs if not r.get("ok")), "by_role": {k: round(v, 3) for k, v in by.items()}}

    dates = sorted({r["date"] for r in rows})[-days:]
    per_day = [{"date": d, **agg([r for r in rows if r["date"] == d])} for d in dates]
    y, w = date.fromisoformat(today).isocalendar()[:2]
    week_rows = [r for r in rows if date.fromisoformat(r["date"]).isocalendar()[:2] == (y, w)]
    cap = day_cap(cfg, today)
    return {"enabled": b["enabled"], "pause": bool(b.get("pause")), "today": {"date": today, "cap": cap, **agg([r for r in rows if r["date"] == today])},
            "week": {"cap": b["weekly_usd"], **agg(week_rows)}, "days": per_day, "total": agg(rows),
            "limits": {"daily_usd": b["daily_usd"], "weekend_usd": b["weekend_usd"], "weekly_usd": b["weekly_usd"],
                       "tier3_share": b["tier3_share"]},
            "quota_hits": [{"ts": r["ts"], "reset": r.get("reset")} for r in usage.quota_hits()],
            "shared": bool(usage.shared)}
