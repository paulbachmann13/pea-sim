import json
import subprocess

import pytest

from sim import notify as nt


class Proc:
    def __init__(self, result="ENVOYÉ", code=0, is_error=False):
        self.returncode, self.stdout, self.stderr = code, json.dumps({"result": result, "is_error": is_error}), ""


@pytest.fixture
def n(cfg):
    cfg["notify"].update(email="moi@icloud.com", via_claude_gmail=True, smtp_password="", ntfy_topic="")
    cfg["agents"]["claude_bin"] = "claude-fake"
    return cfg


def run_with(monkeypatch, proc, calls):
    def fake(cmd, **kw):
        calls.append((cmd, kw["input"]))
        if isinstance(proc, Exception):
            raise proc
        return proc
    monkeypatch.setattr(nt.subprocess, "run", fake)


def test_gmail_alert_is_sent_through_claude(monkeypatch, n):
    calls = []
    run_with(monkeypatch, Proc(), calls)
    assert nt.notify(n, "❌ PEA sim : le run a échoué", "boom") is True
    cmd, prompt = calls[0]
    assert "mcp__claude_ai_Gmail__send_message" in cmd and cmd[cmd.index("--tools") + 1] == ""
    assert "Destinataire : moi@icloud.com" in prompt and "Objet : ❌ PEA sim" in prompt and "boom" in prompt


@pytest.mark.parametrize("proc", [Proc("ÉCHEC : connecteur non autorisé"), Proc(code=1), Proc(is_error=True),
                                  subprocess.TimeoutExpired("claude", 180)])
def test_gmail_failure_is_reported_as_not_sent(monkeypatch, n, proc):
    run_with(monkeypatch, proc, [])
    assert nt.notify(n, "t", "m") is False


def test_gmail_disabled_without_address_or_flag(monkeypatch, n):
    calls = []
    run_with(monkeypatch, Proc(), calls)
    n["notify"]["via_claude_gmail"] = False
    assert nt.notify(n, "t", "m") is False and calls == []
    n["notify"].update(via_claude_gmail=True, email="")
    assert nt.notify(n, "t", "m") is False and calls == []
