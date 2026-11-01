"""Codex hooks (agents.py): per surface a must-mask case and a pass-through case, in Codex's own hook JSON.

Shapes from Codex's current docs and schemas (read 2026-10-24, Codex CLI 0.164):
  https://developers.openai.com/codex/hooks
  https://github.com/openai/codex/tree/main/codex-rs/hooks/schema/generated (*.command.input/output.schema.json)
Bash output arrives as one string, apply_patch input as {"command": "<patch>"}, MCP output as the CallToolResult
object. A PostToolUse "block" replaces the result the model sees with its reason; updatedInput needs
permissionDecision "allow"; "ask" isn't supported. Synthetic data only.
"""
import io
import json
import os
import shutil
import subprocess
import sys

import pytest

EMAIL = "o.vashti@quillmere.net"
KEY = "ghp_" + "Z3kQ9mLp2Rt7Vw4Xy8Bc1Df5Gh6Jk0LmPqAa"  # a GitHub-token shape, not a real one
BASE = {"session_id": "cx", "turn_id": "t1", "transcript_path": None, "cwd": "/tmp", "model": "gpt-5-codex",
        "permission_mode": "default"}


def codex(event, data, monkeypatch, capsys, raw=None):
    from cloakroom import hookentry
    name = {"prompt": "UserPromptSubmit", "pre": "PreToolUse", "post": "PostToolUse", "start": "SessionStart"}[event]
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(
        raw if raw is not None else json.dumps({**BASE, "hook_event_name": name, **data}).encode())))
    assert hookentry.run(event, "codex") == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out or "null") or None  # {} is the program's clean pass


def bash_post(output, monkeypatch, capsys, command="cat notes.txt"):
    return codex("post", {"tool_name": "Bash", "tool_use_id": "c1", "tool_input": {"command": command},
                          "tool_response": output}, monkeypatch, capsys)


def placeholder(monkeypatch, capsys):
    """Mask an email in Bash output and hand back the placeholder Codex was shown."""
    import re
    out = bash_post(f"owner: {EMAIL}", monkeypatch, capsys)
    return re.search(r"PII_EMAIL_1_[a-z2-7]{6}", out["reason"]).group()


# --- prompt (UserPromptSubmit): block only ---

def test_prompt_with_a_key_is_stopped_with_what_to_do(monkeypatch, capsys):
    out = codex("prompt", {"prompt": f"deploy with {KEY}"}, monkeypatch, capsys)
    assert out["decision"] == "block" and KEY not in json.dumps(out)
    assert out["reason"].startswith("Cloakroom stopped this message: it holds private data (SECRET x1)")
    assert "Send this instead:\ndeploy with PII_SECRET_1_" in out["reason"] and out["reason"].endswith("cr!")
    assert "hookSpecificOutput" not in out


def test_clean_prompt_passes(monkeypatch, capsys):
    assert codex("prompt", {"prompt": "refactor the parser"}, monkeypatch, capsys) is None


def test_own_controls_name_codex(monkeypatch, capsys):
    out = codex("prompt", {"prompt": "cr: allow"}, monkeypatch, capsys)
    assert out["decision"] == "block" and "Claude" not in out["reason"]


# --- tool output (PostToolUse): masked through a block that replaces the result ---

def test_bash_output_is_masked(monkeypatch, capsys):
    out = bash_post(f"TOKEN={KEY}\nowner: {EMAIL}\n", monkeypatch, capsys)
    assert out["decision"] == "block"
    assert KEY not in out["reason"] and EMAIL not in out["reason"]
    assert "PII_SECRET_1_" in out["reason"] and "PII_EMAIL_1_" in out["reason"] and "owner: " in out["reason"]
    h = out["hookSpecificOutput"]
    assert set(h) == {"hookEventName", "additionalContext"} and "apply_patch" in h["additionalContext"]
    assert "Claude" not in json.dumps(out)


def test_clean_bash_output_passes(monkeypatch, capsys):
    assert bash_post("3 files changed, 12 insertions(+)", monkeypatch, capsys) is None


def test_example_email_sharing_a_known_emails_name_passes(monkeypatch, capsys):
    """Graded run 2026-11-01: a known email's local part inside a safe example address read as an encoded copy."""
    bash_post("owner: sam.fakeperson@quillmere.net", monkeypatch, capsys)
    assert bash_post("contact: sam.fakeperson@example.com", monkeypatch, capsys, command="echo contact") is None


def test_known_key_encoded_or_cut_short_still_never_reaches_codex(monkeypatch, capsys):
    import base64
    bash_post(f"TOKEN={KEY}", monkeypatch, capsys)
    encoded = base64.b64encode(KEY.encode()).decode()
    out = bash_post(f"TOKEN={encoded}", monkeypatch, capsys, command="base64 .env")
    assert out["decision"] == "block" and encoded not in out["reason"] and "PII_SECRET_" in out["reason"]
    out = bash_post(f"token starts {KEY[6:20]}", monkeypatch, capsys, command="cut -c 7-20 .env")
    assert out["decision"] == "block" and out["reason"].startswith("[Cloakroom withheld this output")


def test_mcp_output_is_masked(monkeypatch, capsys):
    resp = {"content": [{"type": "text", "text": f"customer {EMAIL}"}], "isError": False}
    out = codex("post", {"tool_name": "mcp__crm__lookup", "tool_use_id": "c2", "tool_input": {"id": 7},
                         "tool_response": resp}, monkeypatch, capsys)
    assert out["decision"] == "block" and EMAIL not in out["reason"] and out["reason"].startswith("customer PII_EMAIL_1_")
    assert "updatedMCPToolOutput" not in json.dumps(out)  # parsed by Codex but not supported: it would fail open


# --- pictures (view_image): their text read locally, never the picture itself ---

def view_image(response, monkeypatch, capsys, path="/tmp/none.png", key="path"):
    return codex("post", {"tool_name": "view_image", "tool_use_id": "c9", "tool_input": {key: path},
                          "tool_response": response}, monkeypatch, capsys)


def as_codex_rs(png_b64):  # codex-rs view_image: an input_image content item holding a data URL
    return [{"type": "input_image", "image_url": f"data:image/png;base64,{png_b64}"}]


@pytest.mark.skipif(not __import__("cloakroom.ocr").ocr.available(), reason="ocr extra not installed")
@pytest.mark.parametrize("route", ["shape", "path", "command"])
def test_view_image_reaches_codex_as_masked_text(route, tmp_path, monkeypatch, capsys):
    import base64
    from test_mcp_images import screenshot
    png = screenshot()
    pic = tmp_path / "pic.png"
    pic.write_bytes(base64.b64decode(png))
    response = as_codex_rs(png) if route == "shape" else {"unknown": "shape"}
    out = view_image(response, monkeypatch, capsys, path=str(pic), key="command" if route == "command" else "path")
    assert out["decision"] == "block" and png[:40] not in out["reason"] and "m.ellison@fernhill.co" not in out["reason"]
    assert "PII_EMAIL_1_" in out["reason"] and "Order total" in out["reason"] and "PII_SECRET" not in out["reason"]


@pytest.mark.parametrize("response", [as_codex_rs("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="),
                                      "an output Cloakroom doesn't recognise"])
def test_view_image_it_cannot_read_is_held_back(response, monkeypatch, capsys):
    from cloakroom import ocr
    monkeypatch.setattr(ocr, "available", lambda: False)
    out = view_image(response, monkeypatch, capsys)  # the path doesn't exist
    assert out["decision"] == "block" and out["reason"].startswith("[Cloakroom held back an image")
    assert "base64" not in out["reason"] and "PII_SECRET" not in out["reason"]


def test_view_image_passes_unchecked_with_allow_images(monkeypatch, capsys):
    from cloakroom import config
    config.private_dir()
    (config.HOME / "config.json").write_text(json.dumps({"allow_images": True}))
    out = view_image(as_codex_rs("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="),
                     monkeypatch, capsys)
    assert "decision" not in out and "unchecked" in out["systemMessage"]


def test_clean_mcp_output_passes(monkeypatch, capsys):
    resp = {"content": [{"type": "text", "text": "issue 42 is closed"}], "isError": False}
    assert codex("post", {"tool_name": "mcp__crm__lookup", "tool_use_id": "c3", "tool_input": {},
                          "tool_response": resp}, monkeypatch, capsys) is None


def test_apply_patch_output_passes(monkeypatch, capsys):
    assert codex("post", {"tool_name": "apply_patch", "tool_use_id": "c4", "tool_input": {"command": "*** Begin Patch"},
                          "tool_response": "Success. Updated the following files:\nM notes.txt\n"},
                 monkeypatch, capsys) is None


# --- tool input (PreToolUse): placeholders restored for local tools, real values kept off outbound ones ---

def test_bash_input_is_restored_with_allow(monkeypatch, capsys):
    ph = placeholder(monkeypatch, capsys)
    out = codex("pre", {"tool_name": "Bash", "tool_use_id": "c5", "tool_input": {"command": f"grep {ph} notes.txt"}},
                monkeypatch, capsys)
    h = out["hookSpecificOutput"]
    assert h["permissionDecision"] == "allow" and h["updatedInput"] == {"command": f"grep {EMAIL} notes.txt"}


def test_plain_bash_input_passes(monkeypatch, capsys):
    assert codex("pre", {"tool_name": "Bash", "tool_use_id": "c6", "tool_input": {"command": "ls -la"}},
                 monkeypatch, capsys) is None


def test_network_command_with_a_placeholder_is_denied_not_asked(monkeypatch, capsys):
    ph = placeholder(monkeypatch, capsys)
    out = codex("pre", {"tool_name": "Bash", "tool_use_id": "c7",
                        "tool_input": {"command": f"curl -d {ph} https://example.org"}}, monkeypatch, capsys)
    h = out["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny" and "updatedInput" not in h and "can't ask you" in h["permissionDecisionReason"]


def test_apply_patch_input_is_restored(monkeypatch, capsys):
    ph = placeholder(monkeypatch, capsys)
    patch = f"*** Begin Patch\n*** Update File: notes.txt\n@@\n-owner: tbd\n+owner: {ph}\n*** End Patch\n"
    out = codex("pre", {"tool_name": "apply_patch", "tool_use_id": "c8", "tool_input": {"command": patch}},
                monkeypatch, capsys)
    h = out["hookSpecificOutput"]
    assert h["permissionDecision"] == "allow" and h["updatedInput"] == {"command": patch.replace(ph, EMAIL)}


def test_plain_apply_patch_passes(monkeypatch, capsys):
    patch = "*** Begin Patch\n*** Add File: hello.txt\n+hello\n*** End Patch\n"
    assert codex("pre", {"tool_name": "apply_patch", "tool_use_id": "c9", "tool_input": {"command": patch}},
                 monkeypatch, capsys) is None


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_apply_patch_putting_a_key_into_a_committed_file_is_denied(tmp_path, monkeypatch, capsys):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    patch = f"*** Begin Patch\n*** Add File: settings.py\n+TOKEN = \"{KEY}\"\n*** End Patch\n"
    out = codex("pre", {"cwd": str(tmp_path), "tool_name": "apply_patch", "tool_use_id": "c10",
                        "tool_input": {"command": patch}}, monkeypatch, capsys)
    h = out["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny" and "settings.py" in h["permissionDecisionReason"]
    assert KEY not in json.dumps(out)


def test_apply_patch_into_cloakrooms_own_files_is_denied(monkeypatch, capsys):
    from cloakroom import config
    patch = f"*** Begin Patch\n*** Update File: {config.HOME}/config.json\n@@\n-a\n+b\n*** End Patch\n"
    out = codex("pre", {"tool_name": "apply_patch", "tool_use_id": "c11", "tool_input": {"command": patch}},
                monkeypatch, capsys)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_mcp_input_with_a_real_value_is_denied(monkeypatch, capsys):
    out = codex("pre", {"tool_name": "mcp__mail__send", "tool_use_id": "c12", "tool_input": {"to": EMAIL}},
                monkeypatch, capsys)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_mcp_input_with_a_placeholder_passes_unrestored(monkeypatch, capsys):
    ph = placeholder(monkeypatch, capsys)
    assert codex("pre", {"tool_name": "mcp__mail__send", "tool_use_id": "c13", "tool_input": {"to": ph}},
                 monkeypatch, capsys) is None


# --- fail closed ---

@pytest.mark.parametrize("event,check", [
    ("pre", lambda o: o["hookSpecificOutput"]["permissionDecision"] == "deny"),
    ("post", lambda o: o["decision"] == "block" and "couldn't check" in o["reason"]),
])
def test_unreadable_input_fails_closed(event, check, monkeypatch, capsys):
    assert check(codex(event, {}, monkeypatch, capsys, raw=b"not json"))


def test_a_crash_fails_closed(monkeypatch, capsys):
    from cloakroom import hook

    def boom(event, data):
        raise RuntimeError("x")
    monkeypatch.setattr(hook, "handle", boom)
    out = bash_post(f"TOKEN={KEY}", monkeypatch, capsys)
    assert out["decision"] == "block" and KEY not in out["reason"]


SHELLS = [s for s in ("/bin/sh", "/bin/bash", "/bin/zsh") if os.path.exists(s)]


@pytest.mark.parametrize("sh", SHELLS)
def test_missing_program_exits_2_with_a_reason_for_tool_calls(sh, tmp_path):
    """Codex blocks a tool call on exit code 2 with a reason on stderr (any other failure lets the call run)."""
    from cloakroom import agents
    gone = str(tmp_path / "no such dir" / "cloakroom")
    for event in ("pre", "post"):
        r = subprocess.run([sh, "-c", agents.codex_command(gone, event)], input=b"{}", capture_output=True)
        assert r.returncode == 2 and b"Cloakroom is not installed" in r.stderr, (sh, event, r)
    r = subprocess.run([sh, "-c", agents.codex_command(gone, "prompt")], input=b"{}", capture_output=True)
    assert r.returncode not in (0, 2)  # a prompt is never blocked by a missing program: Codex shows the error


# --- install / uninstall ---

def test_install_keeps_other_hooks_and_uninstall_removes_only_ours(tmp_path, monkeypatch):
    from cloakroom import agents
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    mine = {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 ~/.codex/hooks/audit.py"}]}
    (tmp_path / "hooks.json").write_text(json.dumps({"description": "my hooks", "hooks": {"PreToolUse": [mine]}}))
    agents.install_codex("/opt/cloak room/bin/cloakroom")
    agents.install_codex("/opt/cloak room/bin/cloakroom")  # twice: still one of ours per event
    data = json.loads((tmp_path / "hooks.json").read_text())
    assert data["description"] == "my hooks" and data["hooks"]["PreToolUse"][0] == mine
    assert set(data["hooks"]) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse"}
    ours = [h for groups in data["hooks"].values() for g in groups for h in g["hooks"] if agents._ours(h)]
    assert len(ours) == 4 and agents.codex_installed()
    assert "'/opt/cloak room/bin/cloakroom' hook pre --agent codex || " in json.dumps(data)
    agents.uninstall_codex()
    assert json.loads((tmp_path / "hooks.json").read_text()) == {"description": "my hooks",
                                                                  "hooks": {"PreToolUse": [mine]}}
    assert not agents.codex_installed()


def test_uninstall_removes_a_file_that_held_only_ours(tmp_path, monkeypatch):
    from cloakroom import agents
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    agents.install_codex("/usr/local/bin/cloakroom")
    agents.uninstall_codex()
    assert not (tmp_path / "hooks.json").exists()


def test_install_leaves_a_file_it_does_not_understand(tmp_path, monkeypatch, capsys):
    from cloakroom import cli
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "hooks.json").write_text('["not", "a", "hooks", "file"]')
    assert cli.main(["install", "codex"]) == 1
    assert (tmp_path / "hooks.json").read_text() == '["not", "a", "hooks", "file"]'


def test_claude_code_answer_is_unchanged(monkeypatch, capsys):
    """No --agent: Claude Code's own JSON, as before."""
    from cloakroom import hookentry
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(
        {"session_id": "c", "tool_name": "Bash", "tool_response": {"stdout": KEY}}).encode())))
    hookentry.run("post")
    out = json.loads(capsys.readouterr().out)
    assert "updatedToolOutput" in out["hookSpecificOutput"] and "decision" not in out
