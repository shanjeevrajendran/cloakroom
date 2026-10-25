"""0.7.1: Codex long-running-command hold (codex_hold_long_running, default off). Codex stops waiting on a command
after a while and runs no post hook on what it prints after that, so obvious servers, watchers and follows are held
like the egress hold (cr: allow lets one run once)."""
import json

import pytest
from test_codex import codex

from cloakroom import shell

MUST_HOLD = ["tail -f app.log", "tail -F /var/log/system.log", "tail -n 20 -f app.log", "watch -n 1 ls",
             "npm run dev", "npm start", "pnpm dev", "yarn start", "bun run dev", "npm run dev:web", "vite", "npx vite",
             "next dev", "npx next dev", "nodemon server.js", "python -m http.server 8000",
             "python3 manage.py runserver", "docker logs -f web", "docker compose up", "docker-compose up --build",
             "kubectl logs -f api-1", "kubectl port-forward svc/api 8080:80", "journalctl -fu nginx",
             "ping example.com", "uvicorn app:app --reload", "tsc --watch", "flask run", "php -S localhost:8000",
             "cd web && npm run dev", "PORT=3000 npm run dev", "ng serve"]
MUST_NOT_HOLD = ["tail -n 5 app.log", "npm run build", "ping -c 3 example.com", "vite build", "docker logs web",
                 "docker compose up -d", "journalctl -n 50", "git log --follow README.md", "cat watch.txt",
                 "grep dev package.json", "npm test", "npm install", "timeout 5 tail -f app.log",
                 'echo "npm run dev"', "python -m pytest", "next build", "ls -la"]


@pytest.mark.parametrize("cmd", MUST_HOLD)
def test_long_running_is_found(cmd):
    assert shell.long_running(cmd), cmd


@pytest.mark.parametrize("cmd", MUST_NOT_HOLD)
def test_look_alikes_are_not(cmd):
    assert shell.long_running(cmd) == "", cmd


def _turn_on():
    from cloakroom import config
    config.private_dir()
    (config.HOME / "config.json").write_text(json.dumps({"codex_hold_long_running": True}))


def pre(cmd, monkeypatch, capsys):
    return codex("pre", {"tool_name": "Bash", "tool_use_id": "c1", "tool_input": {"command": cmd}}, monkeypatch, capsys)


def decision(out):
    return ((out or {}).get("hookSpecificOutput") or {}).get("permissionDecision")


def test_off_by_default(monkeypatch, capsys):
    assert pre("npm run dev", monkeypatch, capsys) is None


def test_on_holds_with_a_plain_reason_and_counts_an_event(monkeypatch, capsys):
    _turn_on()
    out = pre("tail -f app.log", monkeypatch, capsys)
    assert decision(out) == "deny"
    why = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "tail -f keeps running" in why and "won't be masked" in why and "cr: allow" in why and "Claude" not in why
    from cloakroom import events
    ev = events.read(1)[0]
    assert ev["action"] == "held" and ev["hold"] == "long-running"


def test_on_lets_look_alikes_through(monkeypatch, capsys):
    _turn_on()
    for cmd in ("tail -n 5 app.log", "npm run build", "ping -c 3 example.com"):
        assert decision(pre(cmd, monkeypatch, capsys)) != "deny", cmd


def test_cr_allow_runs_the_same_command_once(monkeypatch, capsys):
    _turn_on()
    assert decision(pre("npm run dev", monkeypatch, capsys)) == "deny"
    codex("prompt", {"prompt": "cr: allow"}, monkeypatch, capsys)
    assert decision(pre("npm run dev", monkeypatch, capsys)) != "deny"
    from cloakroom import events
    assert events.read(1)[0]["action"] == "allowed once"
    assert decision(pre("npm run dev", monkeypatch, capsys)) == "deny"  # once only


def test_claude_code_is_never_held(monkeypatch, capsys):
    """Claude Code runs its post hook on background output too: the hold is Codex's only."""
    import io
    import sys
    _turn_on()
    from cloakroom import hook
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "c", "tool_name": "Bash", "cwd": "/tmp",
                                                              "tool_input": {"command": "npm run dev"}})))
    hook.main("pre")
    assert capsys.readouterr().out.strip() == ""
