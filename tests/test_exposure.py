"""cloakroom exposure: which keys the agents saw, from the event log and from old transcripts. Never a value."""
import io
import json
import os
import sys
import time

import pytest

from test_shell_and_hooks import run_hook

KEY = "ghp_" + "Q7aZ0kLm3Np9Rt2Vw5Xy8Bc1Df4Gh6Jk0LmPq"  # GitHub-token shape, dummy
KEY2 = "sk-ant-" + "api03-Zx9Wq2Er7Ty4Ui1Op6As3Df8Gh5Jk0Lz"  # Anthropic-key shape, dummy
PASSWORD = "Hunter2Hunter2!x"


def _hookentry(event, data, agent, monkeypatch, capsys):
    from cloakroom import hookentry
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(data).encode())))
    hookentry.run(event, agent)
    capsys.readouterr()


def _text(data):
    from cloakroom import exposure
    return exposure.render(data, "text") + exposure.render(data, "json")


@pytest.fixture
def no_history(tmp_path, monkeypatch):
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GEMINI_CLI_HOME", "HOME"):
        monkeypatch.setenv(var, str(tmp_path / ("empty-" + var)))


def test_hint_never_shows_more_than_prefix_and_last_4():
    from cloakroom import exposure
    assert exposure.hint(KEY) == "ghp_…LmPq"
    assert exposure.hint(KEY2) == "sk-ant-…k0Lz"
    assert exposure.hint(PASSWORD) == "…r2!x"
    assert exposure.hint("short-pw-123") == "… (12 chars)"  # under 16: no characters at all


def test_agent_is_logged_from_the_hook_entry(monkeypatch, capsys):
    from cloakroom import events
    _hookentry("post", {"session_id": "a1", "tool_name": "Read", "tool_input": {"file_path": "/tmp/x/.env"},
                        "tool_response": {"file": {"content": f"TOKEN={KEY}"}}}, "claude", monkeypatch, capsys)
    assert events.read(1)[0]["agent"] == "claude"


def test_seen_with_cloakroom(no_history, monkeypatch, capsys):
    from cloakroom import exposure
    _hookentry("post", {"session_id": "s1", "tool_name": "Read",
                        "tool_input": {"file_path": os.path.expanduser("~/code/app/.env")},
                        "tool_response": {"file": {"content": f"TOKEN={KEY}"}}}, "claude", monkeypatch, capsys)
    run_hook("post", {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "cat creds.txt"},
                      "tool_response": {"stdout": f"key {KEY2}", "stderr": ""}}, monkeypatch, capsys)
    run_hook("prompt", {"session_id": "s2", "prompt": f"use {KEY}"}, monkeypatch, capsys)
    data = exposure.build()
    rows = {(r["session"], r["where"], r["action"]): r for r in data["seen"]}
    read = rows[("s1", "Read .env in code", "masked")]
    assert read["agent"] == "claude" and read["kind"] == "SECRET" and read["hint"] == "ghp_…LmPq"
    assert rows[("s1", "command cat", "masked")]["agent"] == "unknown"  # an event from before the agent field
    assert ("s2", "prompt", "denied") in rows
    rotate = {x["hint"]: x for x in data["rotate"]}
    assert rotate["ghp_…LmPq"]["places"] >= 1 and "sk-ant-…k0Lz" in rotate
    text = _text(data)
    assert KEY not in text and KEY2 not in text and KEY[4:20] not in text and "PII_" not in text


@pytest.fixture
def agent_homes(tmp_path, monkeypatch):
    """Dummy transcripts for Claude Code, Codex CLI and Gemini CLI, old enough to scan, plus one in use."""
    old = time.time() - 3 * 86400
    claude = tmp_path / "claude"
    proj = claude / "projects" / "-Users-x-code-app"
    proj.mkdir(parents=True)
    (proj / "a.jsonl").write_text(json.dumps({"message": {"content": f"use {KEY} please"}}) + "\nnot json "
                                  + KEY2 + "\n")
    (proj / "busy.jsonl").write_text(json.dumps({"message": {"content": f"key {KEY}"}}) + "\n")
    (claude / "history.jsonl").write_text(json.dumps({"display": f"password: {PASSWORD}"}) + "\n")
    codex = tmp_path / "codex"
    (codex / "sessions" / "2026" / "10").mkdir(parents=True)
    (codex / "sessions" / "2026" / "10" / "r.jsonl").write_text(
        json.dumps({"payload": {"output": f"export ANTHROPIC_API_KEY={KEY2}"}}) + "\n")
    gemini = tmp_path / "ghome" / ".gemini" / "tmp" / "abc" / "chats"
    gemini.mkdir(parents=True)
    (gemini / "session-1.json").write_text(json.dumps({"messages": [{"content": f"token {KEY}"}]}))
    files = [proj / "a.jsonl", claude / "history.jsonl", codex / "sessions/2026/10/r.jsonl", gemini / "session-1.json"]
    for p in files:
        os.utime(p, (old, old))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "ghome"))
    monkeypatch.setenv("HOME", str(tmp_path / "chome"))
    return files


def test_history_scan_finds_keys_in_each_agent_and_reads_only(agent_homes):
    from cloakroom import exposure
    before = {p: (p.read_bytes(), p.stat().st_mtime) for p in agent_homes}
    data = exposure.build()
    got = {(r["agent"], r["hint"]) for r in data["history"]}
    assert {("claude", "ghp_…LmPq"), ("claude", "sk-ant-…k0Lz"), ("codex", "sk-ant-…k0Lz"),
            ("gemini", "ghp_…LmPq")} <= got
    assert any(r["hint"] == "…r2!x" and r["file"].endswith("history.jsonl") for r in data["history"])
    assert data["skipped"]["busy"] == 1 and not any(r["file"].endswith("busy.jsonl") for r in data["history"])
    rotate = {x["hint"]: x["places"] for x in data["rotate"]}
    assert rotate["ghp_…LmPq"] == 2 and rotate["sk-ant-…k0Lz"] == 2  # one line per key, not per file
    assert {p: (p.read_bytes(), p.stat().st_mtime) for p in agent_homes} == before
    text = _text(data)
    for value in (KEY, KEY2, PASSWORD, KEY[4:20], KEY2[7:24]):
        assert value not in text
    assert "cloakroom clean-history" in text


def test_history_files_lists_cursor_transcripts(tmp_path, monkeypatch, no_history):
    from cloakroom import exposure
    home = tmp_path / "chome"
    t = home / ".cursor" / "projects" / "Users-x-app" / "agent-transcripts" / "s1"
    t.mkdir(parents=True)
    (t / "s1.jsonl").write_text(json.dumps({"role": "user", "message": {"content": f"key {KEY}"}}) + "\n")
    (t.parent.parent / "other.txt").write_text("not a transcript")
    monkeypatch.setenv("HOME", str(home))
    assert exposure.history_files() == [("cursor", t / "s1.jsonl")]


def test_since_skips_older_files(agent_homes):
    from cloakroom import exposure
    assert exposure.build(since="2999-01-01")["history"] == []


def test_a_missed_secret_is_not_claimed_safe(tmp_path, monkeypatch, no_history):
    """A value the rules don't catch leaves no line, and the output says so instead of calling the rest safe."""
    from cloakroom import exposure
    proj = tmp_path / "claude" / "projects" / "p"
    proj.mkdir(parents=True)
    t = proj / "t.jsonl"
    t.write_text(json.dumps({"message": {"content": "my bank login is bluegiraffe and the pin is the usual"}}) + "\n")
    os.utime(t, (time.time() - 3600,) * 2)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    data = exposure.build()
    assert data["history"] == [] and data["rotate"] == []
    text = exposure.render(data)
    assert exposure.MISSED in text and "not proof it is safe" in text
    lowered = text.replace(exposure.MISSED, "").lower()
    for claim in ("safe", "no secrets", "nothing exposed", "all clear", "clean"):
        assert claim not in lowered
    assert exposure.MISSED == json.loads(exposure.render(data, "json"))["about"]


def test_cli(agent_homes, capsys):
    from cloakroom import cli
    assert cli.main(["exposure"]) == 0
    out = capsys.readouterr().out
    assert "Rotate these" in out and KEY not in out
    assert cli.main(["exposure", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["history"]
