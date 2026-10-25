"""Gemini CLI hooks (agents.py): per surface a must-mask case and a pass-through case, in Gemini's own hook JSON.

Shapes from Gemini CLI's docs and source (read 2026-10-24, Gemini CLI 0.65.0):
  https://geminicli.com/docs/hooks/reference (input and output fields per event, exit codes)
  https://github.com/google-gemini/gemini-cli/tree/v0.65.0/packages/core/src/hooks (types.ts, hookRunner.ts)
  https://github.com/google-gemini/gemini-cli/blob/v0.65.0/packages/core/src/core/coreToolHookTriggers.ts
  https://github.com/google-gemini/gemini-cli/blob/v0.65.0/packages/core/src/scheduler/hook-utils.ts
Every hook gets session_id, transcript_path, cwd, hook_event_name and timestamp. BeforeAgent gets the prompt;
BeforeTool tool_name, tool_input (and mcp_context for MCP tools, named mcp_<server>_<tool>); AfterTool also
tool_response {llmContent, returnDisplay, error}, where llmContent is a string or Gemini Parts ({text},
{inlineData: {mimeType, data}}). Answers: decision "deny" + reason (BeforeAgent: the message is dropped; AfterTool:
the reason replaces the result), "ask", and hookSpecificOutput.tool_input (the whole new input). Synthetic data only.
"""
import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

EMAIL = "o.vashti@quillmere.net"
KEY = "ghp_" + "Z3kQ9mLp2Rt7Vw4Xy8Bc1Df5Gh6Jk0LmPqAa"  # a GitHub-token shape, not a real one
BASE = {"session_id": "gm", "transcript_path": "/tmp/gm.json", "cwd": "/tmp", "timestamp": "2026-10-24T20:00:00Z"}
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 32).decode()


def gemini(event, data, monkeypatch, capsys, raw=None):
    from cloakroom import agents, hookentry
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(
        raw if raw is not None else json.dumps({**BASE, "hook_event_name": agents.GEMINI_EVENTS[event],
                                                **data}).encode())))
    assert hookentry.run(event, "gemini") == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def after(tool, llm, monkeypatch, capsys, tool_input=None, **extra):
    return gemini("post", {"tool_name": tool, "tool_input": tool_input or {},
                           "tool_response": {"llmContent": llm, "returnDisplay": "shown to you"}, **extra},
                  monkeypatch, capsys)


def before(tool, tool_input, monkeypatch, capsys, **extra):
    return gemini("pre", {"tool_name": tool, "tool_input": tool_input, **extra}, monkeypatch, capsys)


def placeholder(monkeypatch, capsys):
    """Mask an email in shell output and hand back the placeholder Gemini was shown."""
    out = after("run_shell_command", f"owner: {EMAIL}", monkeypatch, capsys, {"command": "cat notes.txt"})
    return re.search(r"PII_EMAIL_1_[a-z2-7]{6}", out["reason"]).group()


# --- prompt (BeforeAgent): block only ---

def test_prompt_with_a_key_is_stopped_with_what_to_do(monkeypatch, capsys):
    out = gemini("prompt", {"prompt": f"deploy with {KEY}"}, monkeypatch, capsys)
    assert out["decision"] == "deny" and KEY not in json.dumps(out)
    assert out["reason"].startswith("Cloakroom stopped this message: it holds SECRET")
    assert "ask Gemini CLI to read it" in out["reason"] and "cr!" in out["reason"]
    assert "hookSpecificOutput" not in out


def test_clean_prompt_passes(monkeypatch, capsys):
    assert gemini("prompt", {"prompt": "refactor the parser"}, monkeypatch, capsys) is None


def test_own_controls_name_gemini(monkeypatch, capsys):
    out = gemini("prompt", {"prompt": "cr: allow"}, monkeypatch, capsys)
    assert out["decision"] == "deny" and "Claude" not in out["reason"]


# --- tool output (AfterTool): masked through a deny whose reason replaces the result ---

def test_shell_output_is_masked_with_the_guide_in_the_reason(monkeypatch, capsys):
    out = after("run_shell_command", f"TOKEN={KEY}\nowner: {EMAIL}\n", monkeypatch, capsys, {"command": "cat .env"})
    assert out["decision"] == "deny" and KEY not in out["reason"] and EMAIL not in out["reason"]
    assert "PII_SECRET_1_" in out["reason"] and "owner: PII_EMAIL_1_" in out["reason"]
    assert "write_file" in out["reason"]  # the guide: additionalContext is dropped on a deny
    assert "hookSpecificOutput" not in out and "Claude" not in json.dumps(out)


def test_clean_shell_output_passes(monkeypatch, capsys):
    assert after("run_shell_command", "3 files changed", monkeypatch, capsys, {"command": "git diff --stat"}) is None


def test_read_file_output_is_masked(monkeypatch, capsys):
    out = after("read_file", f"admin_email = {EMAIL}\n", monkeypatch, capsys, {"file_path": "/tmp/app.cfg"})
    assert out["decision"] == "deny" and out["reason"].startswith("admin_email = PII_EMAIL_1_")


def test_clean_read_file_output_passes(monkeypatch, capsys):
    assert after("read_file", "def main():\n    pass\n", monkeypatch, capsys, {"file_path": "/tmp/a.py"}) is None


def test_mcp_output_parts_are_masked(monkeypatch, capsys):
    out = after("mcp_crm_lookup", [{"text": f"customer {EMAIL}"}], monkeypatch, capsys, {"id": 7},
                mcp_context={"server_name": "crm", "tool_name": "lookup", "command": "crm-server"})
    assert out["decision"] == "deny" and out["reason"].startswith("customer PII_EMAIL_1_") and EMAIL not in out["reason"]


def test_clean_mcp_output_passes(monkeypatch, capsys):
    assert after("mcp_crm_lookup", [{"text": "issue 42 is closed"}], monkeypatch, capsys, {},
                 mcp_context={"server_name": "crm", "tool_name": "lookup"}) is None


def test_a_picture_is_never_passed_as_it_is(monkeypatch, capsys):
    out = after("read_file", {"inlineData": {"mimeType": "image/png", "data": PNG}}, monkeypatch, capsys,
                {"file_path": "/tmp/shot.png"})
    assert out["decision"] == "deny" and PNG not in json.dumps(out)  # OCR text or held back, never the pixels


@pytest.mark.parametrize("part", [{"inlineData": {"mimeType": "application/pdf", "data": "JVBERi0xLjQK"}},
                                  {"fileData": {"mimeType": "audio/mpeg", "fileUri": "file:///tmp/a.mp3"}}])
def test_a_part_cloakroom_cant_read_is_held_back(part, monkeypatch, capsys):
    out = after("read_file", [{"text": "Here is the file:"}, part], monkeypatch, capsys, {"file_path": "/tmp/x"})
    assert out["decision"] == "deny" and "held back a" in out["reason"] and "JVBERi0" not in out["reason"]
    assert out["reason"].startswith("Here is the file:")


# --- tool input (BeforeTool): placeholders restored for local tools, real values kept off outbound ones ---

def test_shell_input_is_restored(monkeypatch, capsys):
    ph = placeholder(monkeypatch, capsys)
    args = {"command": f"grep {ph} notes.txt", "description": "find the owner"}
    out = before("run_shell_command", args, monkeypatch, capsys)
    assert out["hookSpecificOutput"] == {"hookEventName": "BeforeTool", "tool_input": {
        "command": f"grep {EMAIL} notes.txt", "description": "find the owner"}}  # the whole input: it replaces
    assert "decision" not in out


def test_plain_shell_input_passes(monkeypatch, capsys):
    assert before("run_shell_command", {"command": "ls -la"}, monkeypatch, capsys) is None


def test_network_command_with_a_placeholder_is_asked_unrestored(monkeypatch, capsys):
    ph = placeholder(monkeypatch, capsys)
    out = before("run_shell_command", {"command": f"curl -d {ph} https://example.org"}, monkeypatch, capsys)
    assert out["decision"] == "ask" and "hookSpecificOutput" not in out and "off this machine" in out["systemMessage"]


def test_unknown_command_is_restored_and_asked(monkeypatch, capsys):
    ph = placeholder(monkeypatch, capsys)
    out = before("run_shell_command", {"command": f"frobnicate {ph}"}, monkeypatch, capsys)
    assert out["decision"] == "ask" and out["hookSpecificOutput"]["tool_input"] == {"command": f"frobnicate {EMAIL}"}


def test_write_file_and_replace_are_restored(monkeypatch, capsys):
    ph = placeholder(monkeypatch, capsys)
    out = before("write_file", {"file_path": "/tmp/owner.txt", "content": f"owner: {ph}\n"}, monkeypatch, capsys)
    assert out["hookSpecificOutput"]["tool_input"] == {"file_path": "/tmp/owner.txt", "content": f"owner: {EMAIL}\n"}
    out = before("replace", {"file_path": "/tmp/owner.txt", "old_string": "owner: tbd", "new_string": f"owner: {ph}",
                             "instruction": "set the owner"}, monkeypatch, capsys)
    assert out["hookSpecificOutput"]["tool_input"]["new_string"] == f"owner: {EMAIL}"


def test_plain_write_file_passes(monkeypatch, capsys):
    assert before("write_file", {"file_path": "/tmp/hello.txt", "content": "hello\n"}, monkeypatch, capsys) is None


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_write_file_putting_a_key_into_a_committed_file_is_denied(tmp_path, monkeypatch, capsys):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    out = before("write_file", {"file_path": str(tmp_path / "settings.py"), "content": f'TOKEN = "{KEY}"\n'},
                 monkeypatch, capsys, cwd=str(tmp_path))
    assert out["decision"] == "deny" and "settings.py" in out["reason"] and KEY not in json.dumps(out)


def test_write_into_cloakrooms_own_files_is_denied(monkeypatch, capsys):
    from cloakroom import config
    out = before("write_file", {"file_path": f"{config.HOME}/config.json", "content": "{}"}, monkeypatch, capsys)
    assert out["decision"] == "deny" and "Gemini CLI can't change them" in out["reason"]


def test_mcp_input_with_a_real_value_is_denied(monkeypatch, capsys):
    out = before("mcp_mail_send", {"to": EMAIL}, monkeypatch, capsys,
                 mcp_context={"server_name": "mail", "tool_name": "send"})
    assert out["decision"] == "deny" and "mcp__mail__send" in out["reason"]


def test_mcp_input_with_a_placeholder_passes_unrestored(monkeypatch, capsys):
    ph = placeholder(monkeypatch, capsys)
    assert before("mcp_mail_send", {"to": ph}, monkeypatch, capsys,
                  mcp_context={"server_name": "mail", "tool_name": "send"}) is None


# --- fail closed, and never waiting for the model ---

@pytest.mark.parametrize("event,check", [
    ("pre", lambda o: o["decision"] == "deny"),
    ("post", lambda o: o["decision"] == "deny" and "couldn't check" in o["reason"]),
    ("prompt", lambda o: "NOT fully checked" in o["systemMessage"] and "decision" not in o),  # never locked out
])
def test_unreadable_input_fails_closed(event, check, monkeypatch, capsys):
    assert check(gemini(event, {}, monkeypatch, capsys, raw=b"not json"))


def test_a_crash_fails_closed(monkeypatch, capsys):
    from cloakroom import hook

    def boom(event, data):
        raise RuntimeError("x")
    monkeypatch.setattr(hook, "handle", boom)
    out = after("run_shell_command", f"TOKEN={KEY}", monkeypatch, capsys, {"command": "env"})
    assert out["decision"] == "deny" and KEY not in out["reason"]


def test_a_cold_service_is_not_waited_for(monkeypatch, capsys):
    """Gemini lets the original through when a hook times out: with the model not loaded, the rules answer."""
    import socket
    from cloakroom import client
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # free once closed: nothing listens there
    monkeypatch.setenv("CLOAKROOM_MODE", "full")
    monkeypatch.setenv("CLOAKROOM_PORT", str(port))
    started = []
    monkeypatch.setattr(client, "start_background", lambda: started.append(1))
    out = after("run_shell_command", f"TOKEN={KEY}", monkeypatch, capsys, {"command": "env"})
    assert out["decision"] == "deny" and KEY not in out["reason"] and "PII_SECRET_1_" in out["reason"]
    assert "rules only" in out["systemMessage"] and started == [1]


@pytest.mark.parametrize("agent", ["codex", "gemini"])
@pytest.mark.parametrize("event", ["start", "prompt", "pre", "post"])
def test_an_empty_answer_is_a_clean_pass(agent, event):
    """`cloakroom hook` may print {} for a clean pass (fix/windows-tests): no decision comes out of it."""
    from cloakroom import agents
    assert not agents.answer(agent, event, {}, {"tool_response": "ok"})


SHELLS = [s for s in ("/bin/sh", "/bin/bash", "/bin/zsh") if os.path.exists(s)]


@pytest.mark.parametrize("sh", SHELLS)
def test_missing_program_blocks_tool_calls_but_not_prompts(sh, tmp_path):
    """Gemini blocks on exit 2 with a reason on stderr; any other failure lets a tool call run. A prompt answered in
    plain text with another non-zero exit would be blocked, so its fallback answers JSON with exit 0."""
    from cloakroom import agents
    gone = str(tmp_path / "no such dir" / "cloakroom")
    for event in ("pre", "post"):
        r = subprocess.run([sh, "-c", agents.gemini_command(gone, event)], input=b"{}", capture_output=True)
        assert r.returncode == 2 and b"Cloakroom is not installed" in r.stderr, (sh, event, r)
    for event in ("prompt", "start"):
        r = subprocess.run([sh, "-c", agents.gemini_command(gone, event)], input=b"{}", capture_output=True)
        assert r.returncode == 0 and json.loads(r.stdout) == {"systemMessage": agents.NOT_CHECKED}, (sh, event, r)


# --- install / uninstall: an extension folder of our own ---

def test_install_writes_only_our_extension_and_uninstall_removes_it(tmp_path, monkeypatch, capsys):
    from cloakroom import agents, cli
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path))
    monkeypatch.setattr(agents, "_program", lambda: "/opt/cloak room/bin/cloakroom")
    other = tmp_path / ".gemini" / "extensions" / "other"
    other.mkdir(parents=True)
    (other / "gemini-extension.json").write_text('{"name": "other", "version": "1"}')
    (tmp_path / ".gemini" / "settings.json").write_text('{"hooks": {}} // mine\n')
    assert cli.main(["install", "gemini"]) == 0
    assert cli.main(["install", "gemini"]) == 0  # twice: same files
    assert "cloakroom uninstall gemini" in capsys.readouterr().out
    ext = tmp_path / ".gemini" / "extensions" / "cloakroom"
    assert json.loads((ext / "gemini-extension.json").read_text())["name"] == "cloakroom"
    hooks = json.loads((ext / "hooks" / "hooks.json").read_text())["hooks"]
    assert set(hooks) == {"SessionStart", "BeforeAgent", "BeforeTool", "AfterTool"}
    pre = hooks["BeforeTool"][0]["hooks"][0]
    assert pre["command"].startswith("'/opt/cloak room/bin/cloakroom' hook pre --agent gemini || ")
    assert pre["timeout"] == 60000 and "matcher" not in hooks["BeforeTool"][0]  # every tool
    assert agents.gemini_installed()
    assert cli.main(["uninstall", "gemini"]) == 0 and not ext.exists() and not agents.gemini_installed()
    assert (other / "gemini-extension.json").exists()
    assert (tmp_path / ".gemini" / "settings.json").read_text() == '{"hooks": {}} // mine\n'
    assert cli.main(["uninstall", "gemini"]) == 0  # nothing left to remove


def test_someone_elses_cloakroom_extension_is_left_alone(tmp_path, monkeypatch):
    from cloakroom import agents, cli
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path))
    monkeypatch.setattr(agents, "_program", lambda: "/usr/local/bin/cloakroom")
    ext = tmp_path / ".gemini" / "extensions" / "cloakroom"
    ext.mkdir(parents=True)
    (ext / "gemini-extension.json").write_text('{"name": "cloakroom", "version": "9"}')
    assert cli.main(["install", "gemini"]) == 1 and cli.main(["uninstall", "gemini"]) == 1
    assert (ext / "gemini-extension.json").read_text() == '{"name": "cloakroom", "version": "9"}'
    assert not (ext / "hooks").exists()
