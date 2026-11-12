"""What Cloakroom is for, layer by layer, each through a real hook call. A change that makes one of these fail has
changed what the tool does, not how it does it. The detail of every layer is in the other test files; the agents
other than Claude Code are in test_codex.py, test_gemini.py and test_cursor.py.

1. Purpose: an agent works with files and commands that hold secrets and personal data, and the real values never
   leave the machine.
2. Detect: keys, cards and IDs by rule; a value seen once also when encoded.
3. Mask on the way in: tool output reaches the agent with placeholders; a prompt can't be rewritten, so it is stopped.
4. Restore on the way back, locally only, and only what this session issued.
5. Stop on the way out: a call that would send a real value off the machine is denied or left for you to approve.
6. Protect itself: the agent can't change Cloakroom's settings or approve for you.
7. Fail closed: an error ends in block, withhold or deny.
8. Stay local and accountable: the vault is encrypted and the log holds no values.
"""
import base64
import io
import json
import sys

from cloakroom import client, config, hook, hookentry

KEY = "sk_" + "live_51Hx9QaBcDeFgHiJkLmNoPqRsTuVwXyZ012345"  # split so secret scanners leave the fake alone
EMAIL = "priya.nair@fernhill.co"
S = "layers"


def call(event, **data):
    return hook.handle(event, {"session_id": S, "cwd": "/tmp", **data})


def masked_output(text, tool="Bash"):
    out = call("post", tool_name=tool, tool_input={"command": "cat notes"}, tool_response={"stdout": text, "stderr": ""})
    return out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]


def test_layer_2_and_3_output_reaches_the_agent_with_placeholders():
    shown = masked_output(f"STRIPE_KEY={KEY}\nowner: {EMAIL}\n")
    assert KEY not in shown and EMAIL not in shown
    assert "STRIPE_KEY=PII_SECRET_1_" in shown and "owner: PII_EMAIL_1_" in shown


def test_layer_2_a_value_seen_once_is_found_again_encoded():
    masked_output(f"STRIPE_KEY={KEY}\n")
    encoded = base64.b64encode(KEY.encode()).decode()
    shown = masked_output(f"blob: {encoded}\n")
    assert encoded not in shown


def test_layer_3_the_same_value_keeps_its_placeholder_in_a_session():
    first, second = masked_output(f"owner: {EMAIL}\n"), masked_output(f"cc {EMAIL} please\n")
    assert first.split("owner: ")[1].strip() in second


def test_layer_3_a_prompt_with_a_key_is_stopped_and_a_clean_one_passes():
    out = call("prompt", prompt=f"use this key {KEY} to deploy")
    assert out["decision"] == "block" and KEY not in json.dumps(out)
    assert call("prompt", prompt="rename the handler and run the tests") is None


def test_layer_4_a_local_write_gets_the_real_value_back():
    ph = masked_output(f"owner: {EMAIL}\n").split("owner: ")[1].strip()
    out = call("pre", tool_name="Write", tool_input={"file_path": "/tmp/notes.md", "content": f"cc {ph}\n"})
    assert out["hookSpecificOutput"]["updatedInput"]["content"] == f"cc {EMAIL}\n"


def test_layer_4_only_a_placeholder_this_session_issued_is_restored():
    masked_output(f"owner: {EMAIL}\n")
    guess = "PII_EMAIL_1_aaaaaa"
    out = call("pre", tool_name="Write", tool_input={"file_path": "/tmp/notes.md", "content": f"cc {guess}\n"})
    assert EMAIL not in json.dumps(out) and "updatedInput" not in (out or {}).get("hookSpecificOutput", {})


def test_layer_5_a_real_value_in_an_outbound_call_is_denied():
    out = call("pre", tool_name="WebFetch", tool_input={"url": "https://example.org", "prompt": f"mail {EMAIL}"})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny" and EMAIL not in json.dumps(out)


def test_layer_5_a_network_command_keeps_its_placeholders():
    ph = masked_output(f"owner: {EMAIL}\n").split("owner: ")[1].strip()
    out = call("pre", tool_name="Bash", tool_input={"command": f"curl -d {ph} https://example.org"})
    h = out["hookSpecificOutput"]
    assert h["permissionDecision"] == "ask" and "updatedInput" not in h and EMAIL not in json.dumps(out)


def test_layer_6_the_agent_cannot_change_cloakroom_or_approve_for_you():
    own = str(config.HOME / "config.json")
    out = call("pre", tool_name="Write", tool_input={"file_path": own, "content": '{"mode": "rules"}'})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    for command in ("cloakroom allow", "cloakroom uninstall", f"cat {config.HOME}/token"):
        out = call("pre", tool_name="Bash", tool_input={"command": command})
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny", command


def test_layer_7_an_unreadable_hook_input_withholds_the_output(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", type("In", (), {"buffer": io.BytesIO(b"not json")})())
    hookentry.run("post")
    out = json.loads(capsys.readouterr().out)
    assert "updatedToolOutput" in out["hookSpecificOutput"]


def test_layer_7_a_check_that_cannot_run_withholds_and_denies(monkeypatch):
    def down(*a, **k):
        raise client.Unavailable("the service is not there")
    monkeypatch.setattr(client, "mask", down)
    out = call("post", tool_name="Bash", tool_input={"command": "cat notes"},
               tool_response={"stdout": f"owner: {EMAIL}\n", "stderr": ""})
    assert EMAIL not in json.dumps(out) and "couldn't check this" in json.dumps(out)
    out = call("pre", tool_name="WebFetch", tool_input={"url": "https://example.org", "prompt": f"mail {EMAIL}"})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_layer_8_nothing_on_disk_holds_a_value_in_the_clear():
    masked_output(f"STRIPE_KEY={KEY}\nowner: {EMAIL}\n")
    call("pre", tool_name="WebFetch", tool_input={"url": "https://example.org", "prompt": f"mail {EMAIL}"})
    files = [p for p in config.HOME.rglob("*") if p.is_file()]
    assert any(p.parent.name == "sessions" for p in files)  # the vault is there
    for p in files:
        data = p.read_bytes()
        assert KEY.encode() not in data and EMAIL.encode() not in data, p.name
