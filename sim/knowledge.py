"""Base de connaissances persistante des agents (dossier knowledge/), relue par tous les agents à chaque run.

playbook.json       règles apprises « si X, alors Y » : datées, avec cas pour / contre, historique des changements
playbook_log.json   journal de toutes les modifications du playbook (qui, quand, pourquoi)
playbook/AAAA-MM-JJ.json  instantané du playbook chaque jour où il change (versions datées)
predictions.json    registre des prédictions du chef, évaluées à l'échéance → calibration de la confiance
notes.json          notes par entreprise (dont l'historique sur 6 mois), par secteur et par thème
insights.json       connaissances accumulées : types de news qui font (ou non) bouger les cours, biais constatés…
agenda.json         événements à venir et scénarios préparés (« si X arrive, alors… »)

Garde-fous contre le sur-apprentissage, appliqués par le code (le LLM ne choisit pas la confiance) :
- une nouvelle règle naît toujours « hypothèse » ;
- la confiance ne monte qu'avec des cas DISTINCTS qui la confirment (un même trade ne compte qu'une fois) ;
- autant (ou plus) de cas contre que de cas pour → « contestée », à réviser ou retirer ;
  2 cas contre de plus que de cas pour → retirée automatiquement ;
- nombre de nouvelles règles par jour et de règles actives plafonné.
"""
import re
from datetime import date
from pathlib import Path

from .journal import read_json, write_json
from .money import D

SCOPES = ["tous", "chef", "actualité", "marché", "risque"]
CONF_RANK = {"confirmée": 0, "probable": 1, "hypothèse": 2, "contestée": 3}


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def counts(rule: dict) -> tuple[int, int]:
    s = sum(1 for e in rule["evidence"] if e["kind"] == "support")
    return s, sum(1 for e in rule["evidence"] if e["kind"] == "contradict")


def confidence(rule: dict) -> str:
    s, c = counts(rule)
    if c and c >= s:
        return "contestée"
    if s >= 4 and c * 4 <= s:
        return "confirmée"
    if s >= 2:
        return "probable"
    return "hypothèse"


class Knowledge:
    def __init__(self, root):
        self.root = Path(root)

    def _load(self, name, default):
        return read_json(self.root / name, default)

    def _save(self, name, data):
        write_json(self.root / name, data)

    # --- Playbook ---------------------------------------------------------------
    def playbook(self) -> dict:
        return self._load("playbook.json", {"rules": [], "next_id": 1})

    def active_rules(self) -> list[dict]:
        return [r for r in self.playbook()["rules"] if r["status"] == "active"]

    def apply_playbook_ops(self, ops: list[dict], day: str, by: str, cfg: dict) -> list[dict]:
        """Applique les propositions d'un agent. Renvoie la liste des changements effectivement faits."""
        k = cfg["knowledge"]
        pb = self.playbook()
        by_id = {r["id"]: r for r in pb["rules"]}
        active = sum(1 for r in pb["rules"] if r["status"] == "active")
        added_today = sum(1 for r in pb["rules"] if r["created"] == day)
        changes = []

        for op in ops:
            kind, reason = op.get("op"), (op.get("reason") or "").strip()
            origin = (op.get("origin") or f"{by} {day}").strip()
            if kind == "add":
                text = (op.get("text") or "").strip()
                if not text:
                    continue
                if any(_norm(r["text"]) == _norm(text) for r in pb["rules"] if r["status"] == "active"):
                    continue
                if added_today >= k["max_new_rules_per_day"] or active >= k["max_active_rules"]:
                    changes.append({"date": day, "by": by, "op": "refusée", "rule": None, "text": text,
                                    "reason": "plafond de nouvelles règles atteint"})
                    continue
                rid = f"R{pb['next_id']}"
                pb["next_id"] += 1
                rule = {"id": rid, "text": text, "scope": op.get("scope") if op.get("scope") in SCOPES else "tous",
                        "status": "active", "created": day, "origin": origin,
                        "evidence": [{"date": day, "kind": "support", "origin": origin, "note": reason}],
                        "history": [{"date": day, "by": by, "change": "création (hypothèse)", "reason": reason}]}
                rule["confidence"] = confidence(rule)
                pb["rules"].append(rule)
                by_id[rid] = rule
                added_today += 1
                active += 1
                changes.append({"date": day, "by": by, "op": "ajout", "rule": rid, "text": text,
                                "confidence": rule["confidence"], "reason": reason, "origin": origin})
                continue

            rule = by_id.get(op.get("rule_id"))
            if not rule or rule["status"] != "active":
                continue
            before = rule["confidence"]
            if kind in ("support", "contradict"):
                if any(e["origin"] == origin for e in rule["evidence"]):
                    continue                                   # même cas déjà compté : pas de double vote
                rule["evidence"].append({"date": day, "kind": kind, "origin": origin, "note": reason})
                label = "cas pour" if kind == "support" else "cas contre"
            elif kind == "revise":
                text = (op.get("text") or "").strip()
                if not text or text == rule["text"]:
                    continue
                label = f"révisée (avant : « {rule['text']} »)"
                rule["text"] = text
            elif kind == "retire":
                rule["status"], rule["retired"] = "retired", day
                label = "retirée"
            else:
                continue
            rule["confidence"] = confidence(rule)
            s, c = counts(rule)
            if rule["status"] == "active" and c >= s + 2:
                rule["status"], rule["retired"] = "retired", day
                label += " → retirée automatiquement (trop de cas contre)"
            rule["history"].append({"date": day, "by": by, "change": f"{label} ({before} → {rule['confidence']})",
                                    "reason": reason})
            changes.append({"date": day, "by": by, "op": label, "rule": rule["id"], "text": rule["text"],
                            "confidence": rule["confidence"], "status": rule["status"], "reason": reason,
                            "origin": origin})

        if changes:
            self._save("playbook.json", pb)
            self._save(f"playbook/{day}.json", pb)                       # version datée
            self._save("playbook_log.json", self._load("playbook_log.json", []) + changes)
        return changes

    def render_playbook(self) -> str:
        rules = sorted(self.active_rules(), key=lambda r: (CONF_RANK[r["confidence"]], r["id"]))
        if not rules:
            return "(aucune règle pour l'instant : le playbook se construit à partir des revues et post-mortems)"
        eff = self.rule_effects()
        lines = []
        for r in rules:
            s, c = counts(r)
            e = eff.get(r["id"])
            effect = f" ; appliquée {e['n']} fois, {e['hits']}/{e['n']} prédictions réussies" if e else ""
            flag = " ⚠ CONTESTÉE : à réviser ou retirer" if r["confidence"] == "contestée" else ""
            lines.append(f"[{r['id']}] ({r['confidence']} : {s} cas pour / {c} contre{effect} ; portée {r['scope']} ; "
                         f"depuis {r['created']}){flag}\n    {r['text']}")
        return "\n".join(lines)

    # --- Prédictions ------------------------------------------------------------
    def predictions(self) -> dict:
        return self._load("predictions.json", {"items": [], "next_id": 1})

    def add_predictions(self, preds: list[dict]) -> list[dict]:
        reg = self.predictions()
        for p in preds:
            p.update(id=f"P{reg['next_id']}", status="open")
            reg["next_id"] += 1
            reg["items"].append(p)
        self._save("predictions.json", reg)
        return preds

    def resolve_predictions(self, store, as_of: str, bench: str) -> list[dict]:
        """Évalue les prédictions arrivées à échéance, au cours de clôture (aucune donnée future)."""
        reg = self.predictions()
        resolved = []
        for p in reg["items"]:
            if p["status"] != "open" or p["target_date"] > as_of:
                continue
            c0, c1 = store.last_close(p["ticker"], p["date"]), store.last_close(p["ticker"], p["target_date"])
            if c0 is None or c1 is None:
                p.update(status="void", resolved_on=as_of)
                continue
            ret = (c1 / c0 - 1) * 100
            b0, b1 = store.last_close(bench, p["date"]), store.last_close(bench, p["target_date"])
            move = D(p["move_pct"])
            hit = ret >= move if p["direction"] == "up" else ret <= -move
            p.update(status="resolved", resolved_on=as_of, return_pct=float(round(ret, 2)), hit=bool(hit),
                     bench_pct=float(round((b1 / b0 - 1) * 100, 2)) if b0 and b1 else None)
            resolved.append(p)
        if resolved:
            self._save("predictions.json", reg)
        return resolved

    def calibration(self) -> dict:
        done = [p for p in self.predictions()["items"] if p["status"] == "resolved"]
        if not done:
            return {"n": 0}
        n = len(done)
        prob = sum(p["probability"] for p in done) / n
        hits = sum(p["hit"] for p in done) / n
        buckets = []
        for lo, hi in ((0, .5), (.5, .6), (.6, .7), (.7, .8), (.8, 1.01)):
            b = [p for p in done if lo <= p["probability"] < hi]
            if b:
                buckets.append({"range": f"{int(lo * 100)}-{min(int(hi * 100), 100)} %", "n": len(b),
                                "announced": round(sum(p["probability"] for p in b) / len(b) * 100),
                                "observed": round(sum(p["hit"] for p in b) / len(b) * 100)})
        return {"n": n, "announced": round(prob * 100), "observed": round(hits * 100),
                "brier": round(sum((p["probability"] - p["hit"]) ** 2 for p in done) / n, 3), "buckets": buckets}

    def render_calibration(self) -> str:
        c = self.calibration()
        if not c["n"]:
            return "(aucune prédiction évaluée pour l'instant)"
        gap = c["announced"] - c["observed"]
        verdict = ("SURCONFIANT" if gap > 10 else "SOUS-CONFIANT" if gap < -10 else "plutôt bien calibré")
        lines = [f"{c['n']} prédictions évaluées : probabilité annoncée en moyenne {c['announced']} %, "
                 f"réussite réelle {c['observed']} % → {verdict} (écart {gap:+d} points). "
                 f"Score de Brier {c['brier']} (0 = parfait, 0,25 = pile ou face)."]
        lines += [f"- tranche {b['range']} : {b['n']} prédictions, annoncé {b['announced']} %, observé {b['observed']} %"
                  for b in c["buckets"]]
        last = [p for p in self.predictions()["items"] if p["status"] == "resolved"][-6:]
        lines += [f"- {p['id']} {p['ticker']} {'hausse' if p['direction'] == 'up' else 'baisse'} ≥ {p['move_pct']} % "
                  f"en {p['horizon_sessions']} séances, proba {round(p['probability'] * 100)} % → "
                  f"{'RÉUSSIE' if p['hit'] else 'ratée'} ({p['return_pct']:+.2f} %, ETF {p['bench_pct']} %)" for p in last]
        return "\n".join(lines)

    def open_predictions_text(self) -> str:
        items = [p for p in self.predictions()["items"] if p["status"] == "open"]
        return "\n".join(f"- {p['id']} ({p['date']}) {p['ticker']} {'hausse' if p['direction'] == 'up' else 'baisse'} "
                         f"≥ {p['move_pct']} % d'ici le {p['target_date']}, proba {round(p['probability'] * 100)} % : "
                         f"{p['thesis'][:160]}" for p in items) or "(aucune)"

    def rule_effects(self) -> dict:
        out = {}
        for p in self.predictions()["items"]:
            if p["status"] != "resolved":
                continue
            for rid in p.get("rules", []):
                e = out.setdefault(rid, {"n": 0, "hits": 0, "returns": []})
                e["n"] += 1
                e["hits"] += int(p["hit"])
                e["returns"].append(p["return_pct"])
        for e in out.values():
            e["avg_return"] = round(sum(e.pop("returns")) / e["n"], 2)
        return out

    # --- Notes entreprise / secteur / thème --------------------------------------
    def notes(self) -> dict:
        return self._load("notes.json", {})

    def has_dossier(self, ticker: str, day: str | None = None, max_age_days: int | None = None) -> bool:
        """Dossier présent (et, si demandé, construit il y a moins de `max_age_days` jours)."""
        n = self.notes().get(f"entreprise:{ticker}", {})
        if not n.get("history_6m"):
            return False
        built = n.get("dossier_built")
        if day and max_age_days and built:
            return (date.fromisoformat(day) - date.fromisoformat(built)).days <= max_age_days
        return True

    # --- Recherche du week-end et plan de la semaine ------------------------------------
    def save_research(self, research: dict, day: str) -> None:
        self._save(f"research/{day}.json", {"date": day, **research})
        self._save("research.json", {"date": day, **research})

    def research(self) -> dict | None:
        return self._load("research.json", None)

    def save_weekplan(self, plan: dict, day: str) -> None:
        self._save(f"weekplan/{day}.json", {"date": day, **plan})
        self._save("weekplan.json", {"date": day, **plan})

    def weekplan(self) -> dict | None:
        return self._load("weekplan.json", None)

    def render_weekplan(self, day: str, max_age_days: int) -> str:
        p = self.weekplan()
        if not p or (date.fromisoformat(day) - date.fromisoformat(p["date"])).days > max_age_days:
            return "(pas de plan de la semaine récent)"
        lines = [f"Plan établi le {p['date']}.", f"Perspectives : {p['outlook']}", f"Lundi : {p['monday_plan']}",
                 f"Cash : {p['cash_policy']}"]
        lines += [f"- événement {e['date'] or 'date à préciser'} : {e['event']} — {e['impact']}" for e in p["key_events"]]
        lines += [f"- scénario ({s['event']}) : si {s['if']} → {s['then']}" for s in p["scenarios"]]
        lines += [f"- à surveiller {w['ticker']} : {w['why']} (entrée si {w['entry_condition']})" for w in p["watchlist"]]
        lines += [f"- risque : {r}" for r in p["risks"]]
        return "\n".join(lines)

    def set_dossier(self, ticker: str, name: str, d: dict, day: str) -> None:
        notes = self.notes()
        n = notes.setdefault(f"entreprise:{ticker}", {"kind": "entreprise", "name": name, "text": "", "versions": []})
        timeline = "\n".join(f"- {e['date']} [{e['kind']}] {e['event']}" for e in d["timeline"])
        n.update(name=name, sector=d["sector"], history_6m=f"{d['summary']}\nChronologie :\n{timeline}",
                 outlook=d["outlook"], dossier_built=day, updated=day)
        self._save("notes.json", notes)

    def update_notes(self, updates: list[dict], day: str, by: str) -> list[str]:
        notes = self.notes()
        changed = []
        for u in updates:
            key, text = (u.get("key") or "").strip(), (u.get("text") or "").strip()
            kind = key.split(":", 1)[0]
            if kind not in ("entreprise", "secteur", "thème") or ":" not in key or not text:
                continue
            n = notes.setdefault(key, {"kind": kind, "name": key.split(":", 1)[1], "text": "", "versions": []})
            if n["text"]:
                n["versions"] = (n.get("versions", []) + [{"date": n.get("updated"), "text": n["text"]}])[-5:]
            n.update(text=text[:1500], updated=day, updated_by=by)
            changed.append(key)
        if changed:
            self._save("notes.json", notes)
        return changed

    def note_keys(self) -> list[str]:
        return list(self.notes())

    def render_notes(self, tickers, max_other: int = 15) -> str:
        notes = self.notes()
        blocks = []
        for t in tickers:
            n = notes.get(f"entreprise:{t}")
            if not n:
                continue
            b = [f"### entreprise:{t} — {n.get('name', t)} (secteur {n.get('sector', '?')}, mis à jour {n.get('updated')})"]
            if n.get("history_6m"):
                b.append(f"Historique 6 mois : {n['history_6m']}")
            if n.get("outlook"):
                b.append(f"Perspectives : {n['outlook']}")
            if n.get("text"):
                b.append(f"Note courante : {n['text']}")
            blocks.append("\n".join(b))
        others = sorted((kv for kv in notes.items() if kv[1]["kind"] != "entreprise"),
                        key=lambda kv: kv[1].get("updated") or "", reverse=True)[:max_other]
        blocks += [f"### {k} (mis à jour {n.get('updated')})\n{n['text']}" for k, n in others]
        return "\n\n".join(blocks) or "(aucune note pour l'instant)"

    # --- Connaissances accumulées ------------------------------------------------
    def insights(self) -> list[dict]:
        return self._load("insights.json", [])

    def add_insights(self, items: list[dict], day: str, by: str, obsolete_ids=()) -> int:
        cur = self.insights()
        for i in cur:
            if i["id"] in obsolete_ids:
                i["status"] = "obsolète"
        seen = {_norm(i["text"]) for i in cur}
        new = []
        for x in items:
            if x.get("text") and _norm(x["text"]) not in seen:
                seen.add(_norm(x["text"]))
                new.append({"id": f"I{len(cur) + len(new) + 1}", "date": day, "by": by, "kind": x["kind"],
                            "subject": x["subject"], "text": x["text"], "status": "active"})
        if new or obsolete_ids:
            self._save("insights.json", cur + new)
        return len(new)

    def render_insights(self, n: int = 30) -> str:
        items = [i for i in self.insights() if i.get("status") == "active"][-n:]
        return "\n".join(f"- [{i['id']}] ({i['kind']}, {i['subject']}, {i['date']}) {i['text']}" for i in items) \
            or "(aucune pour l'instant)"

    # --- Agenda et scénarios ----------------------------------------------------------
    def agenda(self) -> list[dict]:
        return self._load("agenda.json", [])

    def update_agenda(self, events: list[dict], day: str, by: str) -> int:
        cur = self.agenda()
        idx = {(_norm(e["event"]), e.get("date") or ""): e for e in cur}
        added = 0
        def similar(a: dict, ev: dict) -> bool:
            """Même événement reformulé : même date (ou l'une inconnue) et mots en commun."""
            if a["date"] and ev.get("date") and a["date"] != ev["date"]:
                return False
            wa = {w for w in _norm(a["event"]).split() if len(w) > 2}
            wb = {w for w in _norm(ev["event"]).split() if len(w) > 2}
            common = len(wa & wb)
            return common >= 2 and common / min(len(wa), len(wb)) >= 0.6

        for ev in events:
            if not ev.get("event"):
                continue
            key = (_norm(ev["event"]), ev.get("date") or "")
            e = idx.get(key) or next((a for a in cur if similar(a, ev)), None)
            if e is None:
                e = {"date": ev.get("date") or "", "event": ev["event"], "tickers": ev.get("tickers", []),
                     "impact": ev.get("impact", ""), "scenarios": [], "added": day, "by": by}
                cur.append(e)
                idx[key] = e
                added += 1
            if ev.get("if") and ev.get("then"):
                e["scenarios"].append({"if": ev["if"], "then": ev["then"], "date": day})
            elif ev.get("impact"):
                e["impact"] = ev["impact"]
        if added or events:
            self._save("agenda.json", cur)
        return added

    def render_agenda(self, day: str, recent_days: int = 3) -> str:
        items = sorted(self.agenda(), key=lambda e: e["date"] or "9999")
        lines = []
        for e in items:
            if e["date"] and e["date"] < day:
                continue
            when = e["date"] or "date à préciser"
            sc = "".join(f"\n    scénario : si {s['if']} → {s['then']}" for s in e["scenarios"][-3:])
            tick = f" [{', '.join(e['tickers'])}]" if e["tickers"] else ""
            lines.append(f"- {when} : {e['event']}{tick} — {e['impact']}{sc}")
        return "\n".join(lines[:40]) or "(agenda vide)"
