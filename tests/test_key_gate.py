"""An agent can't read Cloakroom's vault key out of the Keychain: /usr/bin/security reading the cloakroom item
(also inside bash -c, $(...) or a pipe into `security -i`), a decrypted dump of the whole Keychain, or keyring
reading the 1.3 item. Other security calls pass. Only strings go to the hook: nothing here runs a command."""
import io
import json
import sys

import pytest

from test_shell_and_hooks import run_hook

HELD = [
    "security find-generic-password -s cloakroom -a vault-key-v2 -w",
    "/usr/bin/security find-generic-password -w -a vault-key-v2 -s cloakroom",
    "security find-generic-password -s cloakroom -w | base64 -d | xxd",
    'bash -c "security find-generic-password -s cloakroom -w"',
    "echo key=$(security find-generic-password -s cloakroom -a vault-key -w)",
    "echo 'find-generic-password -s cloakroom -a vault-key-v2 -w' | security -i",
    "security find-internet-password -s cloakroom",
    "security dump-keychain -d login.keychain",
    "security dump-keychain -rd",
    "keyring get cloakroom vault-key",
    "python3 -c \"import keyring; print(keyring.get_password('cloakroom', 'vault-key'))\"",
]
PASS = [
    "security find-generic-password -s github.com -w",
    "security find-identity -v -p codesigning",
    "security dump-keychain login.keychain",
    "security list-keychains",
    "grep -n find-generic-password src/cloakroom/crypto.py",
    "git commit -m 'docs: keychain notes for cloakroom'",
    "keyring get github.com me",
]


def _pre(command, monkeypatch, capsys):
    return run_hook("pre", {"session_id": "k", "tool_name": "Bash", "tool_input": {"command": command}},
                    monkeypatch, capsys)


@pytest.mark.parametrize("command", HELD)
def test_reading_the_key_is_held(command, monkeypatch, capsys):
    out = _pre(command, monkeypatch, capsys)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny", command
    assert "vault key" in out["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("command", PASS)
def test_other_security_calls_pass(command, monkeypatch, capsys):
    out = _pre(command, monkeypatch, capsys)
    assert not out or out["hookSpecificOutput"]["permissionDecision"] != "deny", command


def test_held_for_codex_and_gemini(monkeypatch, capsys):
    from test_codex import codex
    from test_gemini import before
    out = codex("pre", {"tool_name": "Bash", "tool_input": {"command": HELD[0]}}, monkeypatch, capsys)
    assert "vault key" in json.dumps(out) and "Claude" not in json.dumps(out)
    out = before("run_shell_command", {"command": HELD[0]}, monkeypatch, capsys)
    assert out["decision"] == "deny" and "Gemini CLI can't read it" in out["reason"]


def test_held_for_cursor(monkeypatch, capsys, tmp_path):
    from cloakroom import hookentry
    data = {"conversation_id": "cu", "generation_id": "g1", "session_id": "cu", "workspace_roots": [str(tmp_path)],
            "hook_event_name": "preToolUse", "tool_name": "Shell",
            "tool_input": {"command": HELD[0], "cwd": str(tmp_path), "timeout": 30000}}
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(data).encode())))
    hookentry.run("pre", "cursor")
    out = json.loads(capsys.readouterr().out.strip() or "{}")
    assert out.get("permission") == "deny" and "Cursor can't read it" in out.get("user_message", "")
