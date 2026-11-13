"""Appels LLM via le CLI Claude Code en mode headless (abonnement, pas de clé API).

Chaque appel : aucun outil (sauf la recherche web pour l'agent « research »), aucun serveur MCP, pas de session
persistée, prompt système dédié, réponse JSON imposée par --json-schema. Un `llm` factice peut être injecté pour
les tests. `ask` applique le budget (usage.py), journalise chaque appel et réessaie si la réponse est invalide.
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

from .config import ROOT
from .usage import Usage, quota_reset
from .usage import check as budget_check


class LLMError(RuntimeError):
    pass


class QuotaError(LLMError):
    """Quota de l'abonnement atteint : réessayer tout de suite est inutile."""

    def __init__(self, msg, reset=None):
        super().__init__(msg)
        self.reset = reset


class BudgetSkip(LLMError):
    """Appel non fait pour respecter le budget du projet : une économie voulue, pas une erreur."""

    def __init__(self, role, reason):
        super().__init__(f"{role} : {reason}")
        self.role, self.reason = role, reason


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


def claude_cli(prompt: str, system: str, model: str, schema: dict, cfg: dict, tools: str | None = None) -> tuple[dict, dict]:
    a = cfg["agents"]
    cmd = [resolve_claude_bin(a["claude_bin"]), "-p", "--output-format", "json", "--model", model,
           "--tools", tools or "", "--strict-mcp-config", "--no-session-persistence",
           "--system-prompt", system, "--json-schema", json.dumps(schema)]
    if tools:
        cmd += ["--allowedTools", tools]
    cap = cfg["budget"].get("max_call_usd")
    if cap:
        cmd += ["--max-budget-usd", str(cap)]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           timeout=a["timeout_s"] * (2 if tools else 1))
    except subprocess.TimeoutExpired:
        raise LLMError(f"délai dépassé ({a['timeout_s']} s)")
    if r.returncode != 0:
        if "usage_limit_reached" in r.stdout or "session limit" in r.stdout:
            raise QuotaError("quota de l'abonnement Claude atteint", reset=quota_reset(r.stdout))
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


def default_usage(cfg: dict) -> Usage | None:
    b = cfg["budget"]
    if not b["enabled"]:
        return None
    return Usage(ROOT / b["log"], ROOT / b["shared_log"] if b.get("shared_log") else None)


def ask(role: str, prompt: str, system: str, schema: dict, cfg: dict, check=None, llm=claude_cli,
        tools: str | None = None, usage: Usage | None = None):
    """Appelle le modèle du rôle ; `check(obj)` lève ValueError si la réponse viole une règle métier.
    En cas d'échec, réessaie en expliquant l'erreur. Renvoie (objet, métadonnées des appels).
    Lève BudgetSkip si le budget du projet interdit cet appel."""
    if cfg["budget"].get("pause"):                      # la pause gagne toujours, même budget désactivé
        raise BudgetSkip(role, "pause demandée")
    usage = usage if usage is not None else default_usage(cfg)
    if usage is not None:
        ok, why = budget_check(role, cfg, usage)
        if not ok:
            raise BudgetSkip(role, why)
    model = cfg["agents"]["models"][role]
    extra = {"tools": tools} if tools else {}
    metas, feedback, last = [], "", None
    for _ in range(cfg["agents"]["retries"] + 1):
        try:
            obj, meta = llm(prompt + feedback, system, model, schema, cfg, **extra)
            metas.append({**meta, "role": role})
            if usage is not None:
                usage.record(role=role, model=model, ok=True, tokens_in=meta.get("input_tokens", 0),
                             tokens_out=meta.get("output_tokens", 0), usd=meta.get("cost_usd_equiv") or 0,
                             seconds=meta.get("duration_s", 0))
            if check:
                check(obj)
            return obj, metas
        except QuotaError as e:
            if usage is not None:
                usage.record(role=role, model=model, ok=False, quota=True, reset=e.reset, error=str(e)[:200])
            raise LLMError(f"{role} : {e}")
        except (LLMError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            if usage is not None and not metas:
                usage.record(role=role, model=model, ok=False, error=str(e)[:200])
            last = e
            feedback = f"\n\n## ATTENTION\nTa réponse précédente a été rejetée : {e}\nCorrige et réponds à nouveau."
    raise LLMError(f"{role} : échec après {len(metas) or cfg['agents']['retries'] + 1} tentative(s) : {last}")
