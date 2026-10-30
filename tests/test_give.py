"""`cloakroom give NAME` (handoff.py): a key typed hidden in a terminal reaches local files and commands through the
existing restore, in every agent's hooks, and never appears in a log, event, prompt or message. Synthetic values only."""
import json
import os
import subprocess

import pytest

from test_codex import codex
from test_gemini import before as gemini_before

VALUE = "sk-" + "proj9Hq2LmNp4RvX7yBc1Df5Gh6Jk0Q4z"  # an API-key shape, not a real one
SHOWN = "sk-…Q4z"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "shop"
    (root / ".git").mkdir(parents=True)
    (root / "app").mkdir()
    monkeypatch.chdir(root)
    return root


def give(name, monkeypatch, capsys, value=VALUE, *flags):
    from cloakroom import cli
    monkeypatch.setattr("getpass.getpass", lambda prompt="": value)
    code = cli.main(["give", name, *flags])
    return code, capsys.readouterr()


def make(monkeypatch, capsys, name="STRIPE_KEY"):
    import re
    code, out = give(name, monkeypatch, capsys)
    assert code == 0
    return re.search(r"PII_SECRET_\d+_[a-z2-7]{6}", out.out).group()


def hook(event, data, project, session="s1"):
    from cloakroom import hook as h
    return h.handle(event, {"session_id": session, "cwd": str(project), **data})


def everywhere_in_home():
    from cloakroom import config
    return [p for p in config.HOME.rglob("*") if p.is_file() and VALUE.encode() in p.read_bytes()]


def test_round_trip_through_write_and_bash(project, monkeypatch, capsys):
    ph = make(monkeypatch, capsys)
    out = hook("pre", {"tool_name": "Write", "tool_use_id": "w1",
                       "tool_input": {"file_path": str(project / "app" / ".env"), "content": f"STRIPE_KEY={ph}\n"}}, project)
    written = out["hookSpecificOutput"]["updatedInput"]
    (project / "app" / ".env").write_text(written["content"])  # what Write would do
    assert (project / "app" / ".env").read_text() == f"STRIPE_KEY={VALUE}\n"
    out = hook("pre", {"tool_name": "Bash", "tool_use_id": "b1", "tool_input": {"command": f"echo {ph} > key.txt"}}, project)
    assert out["hookSpecificOutput"]["updatedInput"]["command"] == f"echo {VALUE} > key.txt"


def test_output_showing_the_value_is_masked_back_to_its_placeholder(project, monkeypatch, capsys):
    ph = make(monkeypatch, capsys)
    hook("pre", {"tool_name": "Bash", "tool_input": {"command": f"echo {ph} > k.txt"}}, project)
    out = hook("post", {"tool_name": "Bash", "tool_use_id": "b2", "tool_input": {"command": "cat k.txt"},
                        "tool_response": {"stdout": f"key: {VALUE}\n"}}, project)
    assert VALUE not in json.dumps(out) and ph in json.dumps(out)


def test_logs_events_and_the_session_vault_hold_no_value(project, monkeypatch, capsys):
    ph = make(monkeypatch, capsys)
    hook("pre", {"tool_name": "Write", "tool_use_id": "w2",
                 "tool_input": {"file_path": str(project / "a.env"), "content": ph}}, project)
    hook("post", {"tool_name": "Bash", "tool_use_id": "b3", "tool_input": {"command": "env"},
                  "tool_response": {"stdout": VALUE}}, project)
    assert everywhere_in_home() == []
    from cloakroom import config, dashboard, events
    assert VALUE not in json.dumps(events.read(1000))
    assert VALUE not in (config.HOME / "hook.log").read_text()
    ev = next(e for e in events.read(1000) if ph in e.get("placeholders", []))
    assert dashboard.values(ev["id"])[0]["value"] == ""  # the dashboard never reveals a handed-over value


def test_any_session_in_the_project_restores_it_but_another_project_does_not(project, tmp_path, monkeypatch, capsys):
    ph = make(monkeypatch, capsys)
    cmd = {"tool_name": "Bash", "tool_input": {"command": f"echo {ph}"}}
    assert hook("pre", cmd, project / "app", session="later")["hookSpecificOutput"]["updatedInput"]["command"] == f"echo {VALUE}"
    other = tmp_path / "other"
    (other / ".git").mkdir(parents=True)
    out = hook("pre", cmd, other, session="elsewhere")
    assert VALUE not in json.dumps(out)


def test_a_guessed_placeholder_is_not_restored(project, monkeypatch, capsys):
    ph = make(monkeypatch, capsys)
    guess = ph.rsplit("_", 1)[0] + "_aaaaaa"
    out = hook("pre", {"tool_name": "Bash", "tool_input": {"command": f"echo {guess}"}}, project)
    assert VALUE not in json.dumps(out)


def test_codex_restores_it(project, monkeypatch, capsys):
    ph = make(monkeypatch, capsys)
    out = codex("pre", {"cwd": str(project), "tool_name": "Bash", "tool_use_id": "c1",
                        "tool_input": {"command": f"echo {ph} > key.txt"}}, monkeypatch, capsys)
    h = out["hookSpecificOutput"]
    assert h["permissionDecision"] == "allow" and h["updatedInput"] == {"command": f"echo {VALUE} > key.txt"}
    patch = f"*** Begin Patch\n*** Add File: key.txt\n+{ph}\n*** End Patch\n"
    out = codex("pre", {"cwd": str(project), "tool_name": "apply_patch", "tool_use_id": "c2",
                        "tool_input": {"command": patch}}, monkeypatch, capsys)
    assert out["hookSpecificOutput"]["updatedInput"] == {"command": patch.replace(ph, VALUE)}


def test_gemini_restores_it(project, monkeypatch, capsys):
    ph = make(monkeypatch, capsys)
    out = gemini_before("run_shell_command", {"command": f"echo {ph} > key.txt"}, monkeypatch, capsys, cwd=str(project))
    assert out["hookSpecificOutput"]["tool_input"] == {"command": f"echo {VALUE} > key.txt"}
    out = gemini_before("write_file", {"file_path": str(project / "k"), "content": ph}, monkeypatch, capsys, cwd=str(project))
    assert out["hookSpecificOutput"]["tool_input"]["content"] == VALUE


def test_a_network_command_keeps_the_placeholder(project, monkeypatch, capsys):
    ph = make(monkeypatch, capsys)
    out = hook("pre", {"tool_name": "Bash", "tool_input": {"command": f"curl -d {ph} https://example.org"}}, project)
    assert VALUE not in json.dumps(out)


def test_list_shows_names_and_hints_never_values(project, monkeypatch, capsys):
    ph = make(monkeypatch, capsys)
    make(monkeypatch, capsys, "OTHER")
    code, out = give(None, monkeypatch, capsys, VALUE, "--list")
    assert code == 0 and "STRIPE_KEY" in out.out and "OTHER" in out.out and SHOWN in out.out and ph in out.out
    assert VALUE not in out.out + out.err


def test_giving_a_name_again_replaces_the_value_and_keeps_the_placeholder(project, monkeypatch, capsys):
    ph = make(monkeypatch, capsys)
    code, out = give("STRIPE_KEY", monkeypatch, capsys, VALUE[:-3] + "New")
    assert ph in out.out
    got = hook("pre", {"tool_name": "Bash", "tool_input": {"command": f"echo {ph}"}}, project)
    assert got["hookSpecificOutput"]["updatedInput"]["command"] == f"echo {VALUE[:-3]}New"


def test_forget_removes_it(project, monkeypatch, capsys):
    ph = make(monkeypatch, capsys)
    code, out = give(None, monkeypatch, capsys, VALUE, "--forget", "STRIPE_KEY")
    assert code == 0 and "forgot STRIPE_KEY" in out.out
    out = hook("pre", {"tool_name": "Bash", "tool_input": {"command": f"echo {ph}"}}, project)
    assert VALUE not in json.dumps(out)
    assert give(None, monkeypatch, capsys, VALUE, "--forget", "STRIPE_KEY")[0] == 1


def test_it_expires_after_24_hours(project, monkeypatch, capsys):
    import time
    ph = make(monkeypatch, capsys)
    cmd = {"tool_name": "Bash", "tool_input": {"command": f"echo {ph}"}}
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + 23 * 3600)
    assert hook("pre", cmd, project)["hookSpecificOutput"]["updatedInput"]["command"] == f"echo {VALUE}"
    monkeypatch.setattr(time, "time", lambda: now + 25 * 3600)
    assert VALUE not in json.dumps(hook("pre", cmd, project))
    assert "nothing handed over" in give(None, monkeypatch, capsys, VALUE, "--list")[1].out


def test_hours_flag_sets_the_expiry(project, monkeypatch, capsys):
    from cloakroom import handoff
    give("SHORT", monkeypatch, capsys, VALUE, "--hours", "1")
    left = handoff.entries(str(project))["SHORT"]["expires"] - __import__("time").time()
    assert 3500 < left <= 3600


def test_bad_names_and_empty_values_are_refused(project, monkeypatch, capsys):
    assert give("bad name", monkeypatch, capsys)[0] == 2
    assert give("OK", monkeypatch, capsys, "")[0] == 2
    assert give("a" * 41, monkeypatch, capsys)[0] == 2


@pytest.mark.parametrize("prompt", ["cr: give STRIPE_KEY", "Please cr: give STRIPE_KEY thanks", f"cr: give {VALUE}"])
def test_cr_give_in_a_prompt_is_stopped_with_the_terminal_command(prompt, project):
    out = hook("prompt", {"prompt": prompt}, project)
    assert out["decision"] == "block" and "cloakroom give NAME" in out["reason"] and "\n" not in out["reason"]
    assert VALUE not in json.dumps(out)


def test_cr_give_stop_reaches_codex_and_gemini_users(project, monkeypatch, capsys):
    out = codex("prompt", {"cwd": str(project), "prompt": "cr: give STRIPE_KEY"}, monkeypatch, capsys)
    assert out["decision"] == "block" and "cloakroom give NAME" in out["reason"]
    from test_gemini import gemini
    out = gemini("prompt", {"cwd": str(project), "prompt": "cr: give STRIPE_KEY"}, monkeypatch, capsys)
    assert out["decision"] == "deny" and "cloakroom give NAME" in out["reason"]


def test_a_committed_file_cannot_receive_the_value(project, monkeypatch, capsys):
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    ph = make(monkeypatch, capsys)
    out = hook("pre", {"tool_name": "Write", "tool_use_id": "w9",
                       "tool_input": {"file_path": str(project / "config.py"), "content": f'KEY = "{ph}"\n'}}, project)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_without_give_nothing_is_written_or_changed(project):
    from cloakroom import config
    assert hook("pre", {"tool_name": "Bash", "tool_input": {"command": "ls"}}, project) is None
    assert not (config.HOME / "handoff").exists() or not list((config.HOME / "handoff").glob("*.bin"))


def test_the_session_vault_file_does_not_keep_it(project, monkeypatch, capsys):
    from cloakroom import config, crypto
    ph = make(monkeypatch, capsys)
    hook("post", {"tool_name": "Bash", "tool_use_id": "b4", "tool_input": {"command": "env"},
                  "tool_response": {"stdout": f"{VALUE} and jo.ketterman@quillmere.net"}}, project)
    saved = crypto.decrypt((config.HOME / "sessions" / "s1.json").read_bytes()).decode()
    assert "quillmere" in saved and VALUE not in saved and ph not in saved


def test_the_service_sees_a_value_given_after_its_vault_was_cached(project, monkeypatch, capsys):
    from cloakroom import daemon, handoff
    handoff.note_session("svc", str(project))
    assert daemon._vault("svc").given == {}
    ph = make(monkeypatch, capsys)
    assert daemon._vault("svc").lookup(ph) == VALUE
