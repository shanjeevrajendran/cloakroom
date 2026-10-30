"""A stopped prompt gives you your message back with placeholders to send instead (0.8), on all three agents.

The copy's placeholders are the session's own, so when the agent later uses one in a file, the file on disk gets the
real value. Real values never appear in the message, the logs or the events. Synthetic data only.
"""
import io
import json
import re
import sys
from pathlib import Path

import pytest

from conftest import untag

EMAIL = "o.vashti@quillmere.net"
KEY = "ghp_" + "Z3kQ9mLp2Rt7Vw4Xy8Bc1Df5Gh6Jk0LmPqAa"  # a GitHub-token shape, not a real one
PROMPT = f"Mail {EMAIL} the deploy key {KEY}, thanks Claude"
EVENTS = {"claude": "UserPromptSubmit", "codex": "UserPromptSubmit", "gemini": "BeforeAgent"}
CR = {"claude": "Send it anyway: add cr! at the start. Pause prompt checks: type cr: pause 10m (tool output stays "
                "masked). Resume: cr: resume.",
      "codex": "To send it as it is, start the message with cr!",
      "gemini": "To send it as it is, start the message with cr!"}
NAME = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini CLI"}


def run(agent, event, data, monkeypatch, capsys, session="sti"):
    from cloakroom import hookentry
    name = EVENTS[agent] if event == "prompt" else {"claude": "PreToolUse", "codex": "PreToolUse",
                                                     "gemini": "BeforeTool"}[agent]
    body = {"session_id": session, "cwd": "/tmp", "hook_event_name": name, **data}
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(body).encode())))
    assert hookentry.run(event, agent) == 0
    return json.loads(capsys.readouterr().out.strip() or "null") or None


def copy_of(reason):
    return reason.split("\nSend this instead:\n", 1)[1].rpartition("\n")[0]


@pytest.mark.parametrize("agent", ["claude", "codex", "gemini"])
def test_stop_message_gives_the_prompt_back_with_placeholders(agent, monkeypatch, capsys):
    out = run(agent, "prompt", {"prompt": PROMPT}, monkeypatch, capsys)
    assert out["decision"] == ("deny" if agent == "gemini" else "block")
    assert KEY not in json.dumps(out) and EMAIL not in json.dumps(out)
    stop, rest = out["reason"].split("\n", 1)
    word = "prompt" if agent == "claude" else "message"
    assert stop.startswith(f"Cloakroom stopped this {word}: it holds private data (EMAIL x1, SECRET x1), so it "
                           "wasn't sent. In the copy below that data is replaced by placeholders")
    assert stop.endswith(f"when {NAME[agent]} uses them.")
    assert rest.startswith("Send this instead:\n") and out["reason"].endswith("\n" + CR[agent])
    copy = copy_of(out["reason"])
    assert untag(copy) == "Mail PII_EMAIL_1 the deploy key PII_SECRET_1, thanks Claude"  # the copy is never renamed
    from cloakroom import config
    from cloakroom.vault import Vault
    vault = Vault(config.vault_path("sti"))
    assert {vault.lookup(p) for p in re.findall(r"PII_[A-Z]+_\d+_[a-z2-7]{6}", copy)} == {EMAIL, KEY}
    assert "put that detail in a file" not in out["reason"]  # short: the copy replaces the old advice


@pytest.mark.parametrize("agent", ["claude", "codex", "gemini"])
def test_too_long_falls_back_to_the_advice(agent, monkeypatch, capsys):
    from cloakroom import hook
    out = run(agent, "prompt", {"prompt": PROMPT + " " + "lorem ipsum " * (hook.COPY_MAX // 12)}, monkeypatch, capsys)
    assert "Send this instead" not in out["reason"] and KEY not in out["reason"] and EMAIL not in out["reason"]
    assert "too long to give back here with placeholders (over 10,000 characters)" in out["reason"]
    assert "put the detail in a file" in out["reason"].lower() or "put that detail in a file" in out["reason"]
    assert "cr!" in out["reason"]


def test_just_under_the_limit_still_gets_a_copy(monkeypatch, capsys):
    from cloakroom import hook
    filler = "x " * ((hook.COPY_MAX - 200) // 2)
    out = run("claude", "prompt", {"prompt": PROMPT + " " + filler}, monkeypatch, capsys)
    assert "Send this instead" in out["reason"] and len(copy_of(out["reason"])) <= hook.COPY_MAX


def test_no_copy_when_a_value_was_not_swapped(monkeypatch, capsys):
    """A copy is only given when every value found became a placeholder in it: never the real text."""
    from cloakroom import client
    monkeypatch.setattr(client, "mask", lambda text, session, **k: {
        "text": text, "masked": [["PII_EMAIL_1_aaaaaa", "EMAIL", "email"]], "skipped": []})
    out = run("claude", "prompt", {"prompt": f"mail {EMAIL}"}, monkeypatch, capsys)
    assert out["decision"] == "block" and EMAIL not in out["reason"] and "Send this instead" not in out["reason"]


def test_service_down_gives_the_advice_without_a_copy(monkeypatch, capsys):
    from cloakroom import client, hook

    def down(*a, **k):
        raise client.Unavailable("down")
    monkeypatch.setattr(client, "mask", down)
    out = run("claude", "prompt", {"prompt": PROMPT}, monkeypatch, capsys)
    assert out["decision"] == "block" and out["reason"].endswith(hook.TIP) and KEY not in out["reason"]


def test_gemini_keeps_the_copy_when_there_is_a_system_message(monkeypatch, capsys):
    """Gemini shows a stop's systemMessage instead of its reason: the note goes under the copy instead."""
    from cloakroom import agents
    out = agents.gemini_answer("prompt", {"decision": "block", "systemMessage": "cloakroom: a note",
                                          "reason": "Cloakroom stopped this prompt: it holds private data (EMAIL x1), "
                                                    "so it wasn't sent. x\nSend this instead:\nhi PII_EMAIL_1_aaaaaa\n"
                                                    "Send it anyway: add cr! at the start."})
    assert "systemMessage" not in out and out["reason"].endswith("cr!\n\ncloakroom: a note")
    assert copy_of(out["reason"].rsplit("\n\n", 1)[0]) == "hi PII_EMAIL_1_aaaaaa"


@pytest.mark.parametrize("agent,tool,args", [
    ("claude", "Write", lambda path, text: {"file_path": path, "content": text}),
    ("gemini", "write_file", lambda path, text: {"file_path": path, "content": text}),
])
def test_resent_copy_is_restored_in_the_file_the_agent_writes(agent, tool, args, tmp_path, monkeypatch, capsys):
    stopped = run(agent, "prompt", {"prompt": PROMPT}, monkeypatch, capsys)
    copy = copy_of(stopped["reason"])
    assert run(agent, "prompt", {"prompt": copy}, monkeypatch, capsys) is None  # the copy goes through as it is
    secret = re.search(r"PII_SECRET_1_[a-z2-7]{6}", copy).group()
    path = str(tmp_path / ".env")
    out = run(agent, "pre", {"tool_name": tool, "tool_input": args(path, f"DEPLOY_KEY={secret}\n")}, monkeypatch, capsys)
    h = out["hookSpecificOutput"]
    written = (h.get("updatedInput") or h.get("tool_input"))["content"]
    Path(path).write_text(written)  # what the agent's own tool does with the input Cloakroom gave back
    assert Path(path).read_text() == f"DEPLOY_KEY={KEY}\n"


def test_no_real_value_in_logs_or_events(monkeypatch, capsys):
    from cloakroom import config, events
    for agent in ("claude", "codex", "gemini"):
        run(agent, "prompt", {"prompt": PROMPT}, monkeypatch, capsys)
    stops = [e for e in events.read() if e.get("event") == "prompt"]
    assert len(stops) == 3 and all(e["action"] == "block" and e["kinds"] for e in stops)
    for f in Path(config.HOME).rglob("*"):
        if f.is_file():
            data = f.read_bytes()
            assert KEY.encode() not in data and EMAIL.encode() not in data, f.name
