"""Appels LLM via le CLI Claude Code en mode headless (abonnement, pas de clé API).

Chaque appel : aucun outil, aucun serveur MCP, pas de session persistée, prompt système dédié,
réponse JSON imposée par --json-schema. Un `llm` factice peut être injecté pour les tests.
"""
import json
import shutil
import subprocess
import time
from pathlib import Path


class LLMError(RuntimeError):
    pass


class QuotaError(LLMError):
    """Quota de l'abonnement atteint : réessayer tout de suite est inutile."""


def resolve_claude_bin(setting: str = "auto") -> str:
    if setting and setting != "auto":
        return setting
    found = shutil.which("claude")
    if not found:
        raise LLMError("CLI claude introuvable dans le PATH")
    # Sous Windows, npm installe un script claude.cmd qui lance bin/claude.exe : on appelle l'exe
    # directement pour éviter les problèmes de guillemets de cmd.exe avec le JSON.
    exe = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    return str(exe) if exe.exists() else found


def claude_cli(prompt: str, system: str, model: str, schema: dict, cfg: dict) -> tuple[dict, dict]:
    a = cfg["agents"]
    cmd = [resolve_claude_bin(a["claude_bin"]), "-p", "--output-format", "json", "--model", model,
           "--tools", "", "--strict-mcp-config", "--no-session-persistence",
           "--system-prompt", system, "--json-schema", json.dumps(schema)]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           timeout=a["timeout_s"])
    except subprocess.TimeoutExpired:
        raise LLMError(f"délai dépassé ({a['timeout_s']} s)")
    if r.returncode != 0:
        if "usage_limit_reached" in r.stdout or "session limit" in r.stdout:
            raise QuotaError("quota de l'abonnement Claude atteint (voir l'heure de reset dans le message de claude)")
        raise LLMError(f"claude a échoué (code {r.returncode}) : {(r.stderr or r.stdout)[-500:]}")
    out = json.loads(r.stdout)
    if out.get("is_error") or out.get("structured_output") is None:
        raise LLMError(f"réponse invalide : {str(out.get('result'))[:300]}")
    usage = out.get("usage", {})
    meta = {
        "model": model,
        "duration_s": round(time.time() - t0, 1),
        "input_tokens": sum(usage.get(k, 0) for k in
                            ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
        "output_tokens": usage.get("output_tokens", 0),
        "cost_usd_equiv": out.get("total_cost_usd"),   # indicatif : couvert par l'abonnement
    }
    return out["structured_output"], meta


def ask(role: str, prompt: str, system: str, schema: dict, cfg: dict, check=None, llm=claude_cli):
    """Appelle le modèle du rôle ; `check(obj)` lève ValueError si la réponse viole une règle métier.
    En cas d'échec, réessaie en expliquant l'erreur. Renvoie (objet, métadonnées des appels)."""
    model = cfg["agents"]["models"][role]
    metas, feedback, last = [], "", None
    for _ in range(cfg["agents"]["retries"] + 1):
        try:
            obj, meta = llm(prompt + feedback, system, model, schema, cfg)
            metas.append({**meta, "role": role})
            if check:
                check(obj)
            return obj, metas
        except QuotaError as e:
            raise LLMError(f"{role} : {e}")
        except (LLMError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            last = e
            feedback = f"\n\n## ATTENTION\nTa réponse précédente a été rejetée : {e}\nCorrige et réponds à nouveau."
    raise LLMError(f"{role} : échec après {len(metas) or cfg['agents']['retries'] + 1} tentative(s) : {last}")
