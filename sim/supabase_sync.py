"""Synchronisation des données du dashboard vers Supabase, source de données de la PWA.

Chaque fichier docs/data/**.json devient une ligne de la table pea_docs (clé = chemin relatif).
Seuls les fichiers modifiés depuis la dernière synchro sont envoyés. L'écriture passe par la fonction
pea_sync, protégée par un jeton (local.yaml) : la base n'en connaît que l'empreinte.
"""
import hashlib
import json
from pathlib import Path

import requests

from .journal import read_json, write_json

BATCH_BYTES = 1_500_000


def post_docs(s: dict, docs: dict) -> None:
    r = requests.post(f"{s['url']}/rest/v1/rpc/pea_sync",
                      json={"p_token": s["sync_token"], "p_docs": docs},
                      headers={"apikey": s["publishable_key"]}, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"Supabase {r.status_code} : {r.text[:300]}")


def sync_docs(cfg: dict, docs_data: Path, state_path: Path, post=post_docs) -> int:
    """Envoie les documents modifiés ; renvoie leur nombre."""
    s = cfg["supabase"]
    if not s.get("enabled"):
        return 0
    if not s.get("sync_token"):
        raise RuntimeError("supabase.sync_token manquant dans local.yaml")

    sent = read_json(state_path, {})
    changed = []
    for p in sorted(docs_data.rglob("*.json")):
        key, raw = p.relative_to(docs_data).as_posix(), p.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if sent.get(key) != digest:
            changed.append((key, json.loads(raw), digest, len(raw)))

    batch, size = [], 0
    for item in changed + [None]:
        if item is None or (batch and size + item[3] > BATCH_BYTES):
            if batch:
                post(s, {k: data for k, data, _, _ in batch})
                sent.update({k: d for k, _, d, _ in batch})
                write_json(state_path, sent)          # progression conservée même si un lot échoue ensuite
            batch, size = [], 0
        if item is not None:
            batch.append(item)
            size += item[3]
    return len(changed)
