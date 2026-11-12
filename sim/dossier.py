"""Dossiers entreprises : historique d'actualité sur 6 mois pour chaque titre suivi.

Les titres de presse sont collectés par périodes (Google News, opérateurs after:/before:), filtrés
strictement avant la date de décision (aucune information future), puis un LLM en tire une chronologie
(résultats, annonces, acquisitions, cessions, produits, contrats, direction…), les tendances du secteur
et les perspectives. Le dossier est construit une fois par titre, puis tenu à jour par l'analyste actualité.
"""
from datetime import date, timedelta
from urllib.parse import quote

import requests

from . import agents
from .data import HTTP_HEADERS
from .knowledge import Knowledge
from .llm import claude_cli
from .news import NewsItem, parse_feed


def fetch_history(cfg: dict, name: str, as_of: str) -> list[NewsItem]:
    k = cfg["knowledge"]
    end = date.fromisoformat(as_of)
    start = end - timedelta(days=30 * k["dossier_months"])
    step = (end - start) / k["dossier_windows"]
    items, seen = [], set()
    for i in range(k["dossier_windows"]):
        a, b = start + step * i, start + step * (i + 1)
        url = cfg["news"]["history_feed"].format(name=quote(name), start=a.isoformat(), end=b.isoformat())
        r = requests.get(url, headers=HTTP_HEADERS, timeout=20)
        r.raise_for_status()
        for it in parse_feed(r.content, "historique"):
            if it.published[:10] < as_of and it.title not in seen:      # strictement avant la décision
                seen.add(it.title)
                items.append(it)
    items.sort(key=lambda i: i.published)
    return items[-k["dossier_items_per_ticker"]:]


def build_dossiers(cfg: dict, kb: Knowledge, watch: dict[str, str], as_of: str, llm=claude_cli,
                   fetch=fetch_history) -> tuple[list[str], list[dict], list[str]]:
    """Construit les dossiers manquants (plafonné par run). Renvoie (tickers construits, appels LLM, erreurs)."""
    missing = [t for t in watch if not kb.has_dossier(t)][:cfg["knowledge"]["dossier_max_new_per_run"]]
    if not missing:
        return [], [], []
    blocks, errors, todo = [], [], []
    for t in missing:
        try:
            items = fetch(cfg, watch[t], as_of)
        except Exception as e:
            errors.append(f"historique {t} : {e}")
            continue
        if not items:
            continue
        todo.append(t)
        blocks.append(f"### {t} — {watch[t]}\n" + "\n".join(f"- {i.published[:10]} : {i.title}" for i in items))
    if not todo:
        return [], [], errors
    obj, metas = agents.dossier_writer(cfg, as_of=as_of, companies="\n\n".join(blocks), tickers=todo, llm=llm)
    for d in obj["companies"]:
        kb.set_dossier(d["ticker"], watch[d["ticker"]], d, as_of)
    return todo, metas, errors
