"""0.9: after Cloakroom stops a prompt, the text is taken out of the session's own saved transcript (Claude Code).
Synthetic keys and addresses only; every file lives in a temp folder."""
import io
import json
import os
import sys

import pytest

from test_shell_and_hooks import run_hook

KEY = "ghp_" + "Q7aZ0kLm3Np9Rt2Vw5Xy8Bc1Df4Gh6Jk0LmPq"  # a GitHub-token shape, not a real one
OTHER_KEY = "ghp_" + "R8bA1lMn4Oq0St3Wx6Yz9Cd2Eg5Hi7Kl1NoQr"
EMAIL = "o.vashti@quillmere.net"


@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    root = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    proj = root / "projects" / "-Users-x-app"
    proj.mkdir(parents=True)
    return proj


def transcript(proj, name, *texts):
    path = proj / f"{name}.jsonl"
    path.write_text("".join(json.dumps({"type": "user", "message": {"content": t}}) + "\n" for t in texts))
    return path


def prompt(path, text, monkeypatch, capsys, session="s1"):
    return run_hook("prompt", {"session_id": session, "prompt": text, "transcript_path": str(path)},
                    monkeypatch, capsys)


def lifecycle(event, path, monkeypatch, capsys, session="s1", **extra):
    name = {"stop": "Stop", "end": "SessionEnd"}[event]
    return run_hook("scrub", {"session_id": session, "transcript_path": str(path), "hook_event_name": name, **extra},
                    monkeypatch, capsys)


def test_a_stopped_prompts_key_does_not_stay_in_the_transcript(claude_home, monkeypatch, capsys):
    t = transcript(claude_home, "s1", f"deploy with {KEY} please", "an ordinary line")
    out = prompt(t, f"deploy with {KEY} please", monkeypatch, capsys)
    assert out["decision"] == "block"  # stopped, as before
    text = t.read_text()
    assert KEY not in text and "[SECRET removed by Cloakroom]" in text
    lines = [json.loads(line) for line in text.splitlines()]  # still one JSON object per line
    assert lines[1]["message"]["content"] == "an ordinary line"
    assert "Cloakroom clean-history: 1 key(s) or password(s) in 1 file(s)" in out["systemMessage"]


def test_personal_data_in_a_stopped_prompt_goes_too_but_other_text_stays(claude_home, monkeypatch, capsys):
    t = transcript(claude_home, "s1", f"mail {EMAIL} the report", f"later: {EMAIL} again, and notes")
    prompt(t, f"mail {EMAIL} the report", monkeypatch, capsys)
    text = t.read_text()
    assert EMAIL not in text and "the report" in text and "notes" in text


def test_a_prompt_that_passes_leaves_the_transcript_alone(claude_home, monkeypatch, capsys):
    t = transcript(claude_home, "s1", "tidy the readme")
    before = t.read_text()
    assert prompt(t, "tidy the readme", monkeypatch, capsys) is None
    assert t.read_text() == before


def test_stop_scrubs_a_prompt_written_after_the_hook_ran(claude_home, monkeypatch, capsys):
    t = transcript(claude_home, "s1", "start")
    prompt(t, f"use {KEY}", monkeypatch, capsys)  # the line is not in the file yet
    with open(t, "a") as f:
        f.write(json.dumps({"type": "user", "message": {"content": f"use {KEY}"}}) + "\n")
    out = lifecycle("stop", t, monkeypatch, capsys)
    assert KEY not in t.read_text()
    assert "Cloakroom clean-history: 1 key(s) or password(s)" in out["systemMessage"]


def test_stop_with_nothing_stopped_does_not_touch_the_file(claude_home, monkeypatch, capsys):
    t = transcript(claude_home, "s1", f"has {KEY}")  # a passed prompt (cr!) or old text: not Stop's job every turn
    before = t.read_text()
    assert lifecycle("stop", t, monkeypatch, capsys) is None
    assert t.read_text() == before


def test_session_end_is_the_safety_net(claude_home, monkeypatch, capsys):
    t = transcript(claude_home, "s1", f"cr! use {KEY}", f"and {OTHER_KEY} in a tool result")
    out = lifecycle("end", t, monkeypatch, capsys)
    text = t.read_text()
    assert KEY not in text and OTHER_KEY not in text
    assert "2 key(s) or password(s) in 1 file(s)" in out["systemMessage"]


def test_session_end_clears_the_pending_list(claude_home, monkeypatch, capsys):
    from cloakroom import config
    t = transcript(claude_home, "s1", "start")
    prompt(t, f"use {KEY}", monkeypatch, capsys)
    assert list((config.HOME / "sessions").glob("*.scrub"))
    lifecycle("end", t, monkeypatch, capsys)
    assert not list((config.HOME / "sessions").glob("*.scrub"))


def test_opt_out(claude_home, monkeypatch, capsys):
    from cloakroom import config
    config.private_dir()
    (config.HOME / "config.json").write_text(json.dumps({"scrub_transcript": False}))
    t = transcript(claude_home, "s1", f"use {KEY}")
    before = t.read_text()
    assert prompt(t, f"use {KEY}", monkeypatch, capsys)["decision"] == "block"
    assert lifecycle("end", t, monkeypatch, capsys) is None
    assert t.read_text() == before


def test_nothing_outside_the_sessions_own_file_is_touched(claude_home, tmp_path, monkeypatch, capsys):
    t = transcript(claude_home, "s1", f"use {KEY}")
    sibling = transcript(claude_home, "s2", f"another session holds {KEY}")
    history = claude_home.parent.parent / "history.jsonl"
    history.write_text(json.dumps({"display": f"use {KEY}"}) + "\n")
    elsewhere = tmp_path / "notes.jsonl"
    elsewhere.write_text(json.dumps({"x": f"use {KEY}"}) + "\n")
    not_json_lines = claude_home / "s1.txt"
    not_json_lines.write_text(f"use {KEY}\n")
    kept = {p: p.read_text() for p in (sibling, history, elsewhere, not_json_lines)}
    prompt(t, f"use {KEY}", monkeypatch, capsys)
    for ev in ("stop", "end"):
        lifecycle(ev, elsewhere, monkeypatch, capsys)  # a path outside <claude dir>/projects
        lifecycle(ev, not_json_lines, monkeypatch, capsys)  # not a .jsonl transcript
        lifecycle(ev, claude_home, monkeypatch, capsys)  # a folder
        lifecycle(ev, claude_home / "missing.jsonl", monkeypatch, capsys)
    assert KEY not in t.read_text()
    assert {p: p.read_text() for p in kept} == kept


def test_a_transcript_that_is_being_written_is_not_corrupted(claude_home, monkeypatch, capsys):
    from cloakroom import history
    t = transcript(claude_home, "s1", f"use {KEY}")
    appended = {"n": 0}
    real = history.scan_file

    def scan_then_append(*a, **kw):
        out = real(*a, **kw)
        if not appended["n"]:  # Claude Code writes a line while the scan is running
            appended["n"] += 1
            with open(t, "a") as f:
                f.write(json.dumps({"type": "assistant", "message": {"content": "written meanwhile"}}) + "\n")
        return out

    monkeypatch.setattr(history, "scan_file", scan_then_append)
    prompt(t, f"use {KEY}", monkeypatch, capsys)
    lines = [json.loads(line) for line in t.read_text().splitlines()]
    assert KEY not in t.read_text() and lines[-1]["message"]["content"] == "written meanwhile"
    assert not list(claude_home.glob("*.cloakroom-tmp"))


def test_a_transcript_that_never_stops_changing_is_left_as_it_is(claude_home, monkeypatch, capsys):
    from cloakroom import history
    t = transcript(claude_home, "s1", f"use {KEY}")
    real = history.scan_file

    def always_busy(*a, **kw):
        out = real(*a, **kw)
        with open(t, "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": "more"}}) + "\n")
        return out

    monkeypatch.setattr(history, "scan_file", always_busy)
    assert prompt(t, f"use {KEY}", monkeypatch, capsys)["decision"] == "block"  # still stopped
    assert KEY in t.read_text() and not list(claude_home.glob("*.cloakroom-tmp"))  # tried again at the next stop


def test_scrubs_are_counted_in_the_report(claude_home, monkeypatch, capsys):
    from cloakroom import report
    t = transcript(claude_home, "s1", f"use {KEY}")
    prompt(t, f"use {KEY}", monkeypatch, capsys)
    rows = [r for r in report.rows() if r["action"] == "scrubbed"]
    assert rows and rows[0]["kind"] == "SECRET" and sum(r["count"] for r in rows) == 1
    assert KEY not in report.export("csv")


def test_other_agents_transcripts_are_not_touched(claude_home, monkeypatch, capsys):
    from cloakroom import hookentry
    t = transcript(claude_home, "s1", f"use {KEY}")
    before = t.read_text()
    payload = {"session_id": "cx", "turn_id": "t", "transcript_path": str(t), "cwd": "/tmp", "model": "m",
               "permission_mode": "default", "hook_event_name": "UserPromptSubmit", "prompt": f"use {KEY}"}
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
    assert hookentry.run("prompt", "codex") == 0
    assert t.read_text() == before


def test_the_launchers_and_hook_entry_know_the_new_event(claude_home, monkeypatch, capsys):
    from cloakroom import hookentry
    assert hookentry.closed("scrub", "boom", {}) == {}  # a failure here must not look like a denied tool call
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"not json")))
    assert hookentry.run("scrub") == 0
    assert capsys.readouterr().out.strip() == "{}"
