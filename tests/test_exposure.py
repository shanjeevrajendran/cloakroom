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


def _rec(content, **kw):
    return {"type": "user", "message": {"content": content}, **kw}


def test_typed_filter():
    from cloakroom.exposure import typed
    assert typed(_rec("mail me at a@b.com")) == ["mail me at a@b.com"]
    assert typed(_rec([{"type": "text", "text": "hi"}, {"type": "image"}, {"type": "text", "text": " "}])) == ["hi"]
    assert typed(_rec([{"type": "tool_result", "content": "x"}, {"type": "text", "text": "hi"}])) == []
    assert typed(_rec("hi", isMeta=True)) == []
    assert typed(_rec("<system-reminder>x</system-reminder>")) == []
    assert typed(_rec("  <command-name>/x</command-name>")) == []
    assert typed({"type": "assistant", "message": {"content": "hi"}}) == []
    assert typed("not a dict") == [] and typed(_rec(None)) == []


def test_personal_rows_carry_no_values(tmp_path, monkeypatch, no_history):
    pytest.importorskip("torch")  # the personal pass runs the model
    from cloakroom import exposure
    email = "jane.q.doe.private@gmail.com"
    proj = tmp_path / "claude" / "projects" / "p"
    proj.mkdir(parents=True)
    t = proj / "t.jsonl"
    t.write_text("\n".join(json.dumps(r) for r in [
        _rec(f"please write to {email} about the invoice"),
        _rec([{"type": "tool_result", "content": f"cc {email}"}]),
    ]) + "\n")
    os.utime(t, (time.time() - 3600,) * 2)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    off = exposure.build()
    assert not [r for r in off["history"] if r["kind"] not in exposure.KINDS]
    data = exposure.build(personal=True)
    rows = [r for r in data["history"] if r["kind"] not in exposure.KINDS]
    assert rows and all(r["count"] == 1 and set(r) == set(data["history"][0]) for r in rows)  # tool_result skipped
    assert not any(x["kind"] not in exposure.KINDS for x in data["rotate"])
    text = _text(data)
    assert email not in text and "jane.q.doe" not in text


def test_personal_without_the_model_says_so(tmp_path, monkeypatch, capsys, no_history):
    from cloakroom import cli, engine
    proj = tmp_path / "claude" / "projects" / "p"
    proj.mkdir(parents=True)
    t = proj / "t.jsonl"
    t.write_text(json.dumps(_rec("write to jane@example.org")) + "\n")
    os.utime(t, (time.time() - 3600,) * 2)  # a file changed in the last minutes is skipped as busy
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))

    def no_model(*a, **k):
        raise engine.ModelUnavailable("the model isn't installed")
    monkeypatch.setattr(engine, "detect", no_model)
    assert cli.main(["exposure", "--personal"]) == 2
    assert "--personal needs the model" in capsys.readouterr().err


# Codex, Cursor and Gemini: only what the user typed is read for personal data. Synthetic lines in each agent's shape.
NAME, MAIL = "Marisol Quintanilla", "marisol.q.test@example.org"


def _typed_line(agent):
    said = f"invoice for {NAME}, write to {MAIL}"
    return {
        "codex": {"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "UserMessage", "content": [{"type": "text", "text": said}]}}},
        "cursor": {"role": "user", "message": {"content": [{"type": "text", "text":
                   f"<timestamp>Friday</timestamp>\n<user_query>\n{said}\n</user_query>"}]}},
        "gemini": {"id": "m1", "timestamp": "2026-11-01T10:00:00Z", "type": "user", "content": [{"text": said}]},
    }[agent]


def _other_lines(agent):
    """The same values in tool output, the agent's own text and the agent's injected context: never counted."""
    both = f"{NAME} <{MAIL}>"
    return {
        "codex": [
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                   "content": [{"type": "input_text", "text": f"say {both}"}]}},
            {"type": "response_item", "payload": {"type": "function_call_output", "output": f"git log: {both}"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                   "content": [{"type": "output_text", "text": f"Sent to {both}"}]}},
            {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "AgentMessage", "content": [{"type": "text", "text": f"Done, {both}"}]}}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": f"Done, {both}"}},
        ],
        "cursor": [
            {"role": "assistant", "message": {"content": [{"type": "text", "text": f"Mailing {both}"},
                                                          {"type": "tool_use", "name": "Shell",
                                                           "input": {"command": f"echo {both}"}}]}},
            {"role": "user", "message": {"content": [{"type": "text",
                                                      "text": f"<attached_files>{both}</attached_files>"}]}},
            {"type": "turn_ended", "status": "success"},
        ],
        "gemini": [
            {"type": "gemini", "content": f"Mailing {both}",
             "toolCalls": [{"name": "run_shell_command", "result": [{"functionResponse": {"response": {
                 "output": f"{both}"}}}]}]},
            {"type": "info", "content": f"note {both}"},
            {"$set": {"summary": f"chat with {both}"}},
        ],
    }[agent]


def _write_chat(tmp_path, agent, lines, name="chat.jsonl"):
    where = {"codex": tmp_path / "codex" / "sessions" / "2026" / "11" / "01",
             "cursor": tmp_path / "home" / ".cursor" / "projects" / "p" / "agent-transcripts" / "c1",
             "gemini": tmp_path / "gem" / ".gemini" / "tmp" / "abc123" / "chats"}[agent]
    where.mkdir(parents=True, exist_ok=True)
    f = where / name
    f.write_text(json.dumps(lines) if name.endswith(".json") else "\n".join(json.dumps(x) for x in lines) + "\n")
    os.utime(f, (time.time() - 3600,) * 2)
    return f


@pytest.fixture
def typed_homes(tmp_path, monkeypatch, no_history):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "gem"))


@pytest.fixture
def fake_model(monkeypatch):
    """Finds NAME and MAIL in "full" mode only, so the test runs without the model."""
    from cloakroom import engine, rules

    def detect(text, mode="full", *a, **k):
        if mode != "full":
            return [], []
        hits = [rules.Finding(text.find(v), text.find(v) + len(v), kind, "test")
                for v, kind in ((NAME, "PERSON"), (MAIL, "EMAIL")) if v in text]
        return hits, []
    monkeypatch.setattr(engine, "detect", detect)


@pytest.mark.parametrize("agent", ["codex", "cursor", "gemini"])
def test_personal_reads_what_was_typed_in_each_agent(agent, tmp_path, typed_homes, fake_model):
    from cloakroom import exposure
    _write_chat(tmp_path, agent, _other_lines(agent)[:1] + [_typed_line(agent)] + _other_lines(agent)[1:])
    rows = [r for r in exposure.build(personal=True)["history"] if r["kind"] not in exposure.KINDS]
    assert sorted((r["agent"], r["kind"], r["count"]) for r in rows) == [(agent, "EMAIL", 1), (agent, "PERSON", 1)]
    text = _text(exposure.build(personal=True))
    assert MAIL not in text and NAME not in text and "Quintanilla" not in text


@pytest.mark.parametrize("agent", ["codex", "cursor", "gemini"])
def test_personal_skips_tool_output_and_agent_text(agent, tmp_path, typed_homes, fake_model):
    from cloakroom import exposure
    _write_chat(tmp_path, agent, _other_lines(agent))
    assert not [r for r in exposure.build(personal=True)["history"] if r["kind"] not in exposure.KINDS]


def test_codex_typed_shapes():
    from cloakroom.exposure import codex_typed
    assert codex_typed(_typed_line("codex")) == [f"invoice for {NAME}, write to {MAIL}"]
    assert codex_typed({"type": "event_msg", "payload": {"type": "user_message", "message": "hi there"}}) == ["hi there"]
    assert codex_typed({"session_id": "s", "ts": 1, "text": "from history"}) == ["from history"]  # history.jsonl
    assert codex_typed({"type": "event_msg", "payload": {"type": "user_message", "message": "  "}}) == []
    assert all(codex_typed(x) == [] for x in _other_lines("codex")) and codex_typed("x") == []


def test_cursor_typed_shapes():
    from cloakroom.exposure import cursor_typed
    assert cursor_typed(_typed_line("cursor")) == [f"\ninvoice for {NAME}, write to {MAIL}\n"]
    assert cursor_typed({"role": "user", "message": {"content": "plain words"}}) == ["plain words"]
    assert cursor_typed({"role": "user", "message": {"content": [{"type": "text", "text": "<timestamp>x</timestamp>"}]}}) == []
    assert all(cursor_typed(x) == [] for x in _other_lines("cursor")) and cursor_typed(None) == []


def test_gemini_typed_shapes():
    from cloakroom.exposure import gemini_typed, GEMINI_FILES
    assert gemini_typed({"type": "user", "content": "just a string"}) == ["just a string"]
    # @file expansion: displayContent is what was typed; without it, the referenced-file block is cut off
    assert gemini_typed({"type": "user", "content": [{"text": f"see @a.txt\n{GEMINI_FILES}\n{MAIL}"}],
                         "displayContent": [{"text": "see @a.txt"}]}) == ["see @a.txt"]
    assert gemini_typed({"type": "user", "content": [{"text": f"see @a.txt\n{GEMINI_FILES}\n{MAIL}"}]}) == ["see @a.txt\n"]
    assert gemini_typed({"type": "user", "content": [{"inlineData": {"data": "x"}}]}) == []
    assert gemini_typed({"sessionId": "s", "messageId": 0, "type": "user", "message": "from logs.json"}) == ["from logs.json"]
    assert all(gemini_typed(x) == [] for x in _other_lines("gemini"))


def test_gemini_whole_file_chats(tmp_path, typed_homes, fake_model):
    """Older Gemini CLI writes one JSON document per chat ({"messages": [...]}); logs.json is a plain list."""
    from cloakroom import exposure
    _write_chat(tmp_path, "gemini", {"sessionId": "s", "messages": [_typed_line("gemini")] + _other_lines("gemini")},
                name="session-1.json")
    rows = [r for r in exposure.build(personal=True)["history"] if r["kind"] not in exposure.KINDS]
    assert sorted((r["kind"], r["count"]) for r in rows) == [("EMAIL", 1), ("PERSON", 1)]
