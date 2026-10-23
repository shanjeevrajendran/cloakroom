"""0.6.0 round 2: hash-chained monthly event log, new log events, report, clean-history, MCP inputs."""
import csv
import io
import json
import os
import time

import pytest

from test_shell_and_hooks import run_hook

KEY = "ghp_" + "Q7aZ0kLm3Np9Rt2Vw5Xy8Bc1Df4Gh6Jk0LmPq"  # a GitHub-token shape, not a real one


def _lines():
    from cloakroom import events
    return [line for p in events.months() for line in p.read_text().splitlines()]


# --- 2b log ---------------------------------------------------------------------------------------------------

def test_every_line_names_the_previous_hash():
    from cloakroom import events
    for _ in range(4):
        events.record("pre", {"tool_name": "Bash"}, "pass", [], 1.0)
    lines = _lines()
    assert json.loads(lines[0])["prev"] == "start"
    for a, b in zip(lines, lines[1:]):
        assert json.loads(b)["prev"] == events.line_hash(a)
    r = events.verify()
    assert r["ok"] and r["lines"] == 4 and r["head"] == events.line_hash(lines[-1]) == events.head()


@pytest.mark.parametrize("tamper", ["edit", "delete", "insert"])
def test_tampering_breaks_the_chain(tamper):
    from cloakroom import events
    for _ in range(4):
        events.record("post", {"tool_name": "Read"}, "masked", [("PII_SECRET_1", "SECRET")], 1.0)
    path = events.months()[-1]
    lines = path.read_text().splitlines()
    if tamper == "edit":
        lines[1] = lines[1].replace('"masked"', '"pass"')
    elif tamper == "delete":
        del lines[1]
    else:
        lines.insert(2, lines[1])
    path.write_text("\n".join(lines) + "\n")
    r = events.verify()
    assert not r["ok"] and r["broken"].startswith(path.name + ":")


def test_chain_continues_across_months_and_old_months_are_pruned(monkeypatch):
    from cloakroom import config, events
    config.private_dir()
    (config.HOME / "config.json").write_text(json.dumps({"log_months": 2}))
    clock = {"t": "2026-01-15T10:00:00"}
    monkeypatch.setattr(events.time, "strftime", lambda fmt, *a: clock["t"])
    for month in ("2026-01", "2026-02", "2026-03"):
        clock["t"] = f"{month}-15T10:00:00"
        events.record("pre", {"tool_name": "Bash"}, "pass", [], 1.0)
    assert [p.name for p in events.months()] == ["events-2026-02.jsonl", "events-2026-03.jsonl"]
    pruned = [e for e in events.read() if e["action"] == "pruned"]
    assert len(pruned) == 1 and pruned[0]["target"] == "events-2026-01.jsonl"
    r = events.verify()
    assert r["ok"] and r["anchor"] not in ("", "start")  # the oldest kept line points at a deleted month


def test_parallel_writers_keep_one_chain():
    from concurrent.futures import ProcessPoolExecutor
    from cloakroom import events
    with ProcessPoolExecutor(4) as pool:
        list(pool.map(_write_some, [os.environ["CLOAKROOM_HOME"]] * 4))
    assert events.verify()["ok"] and events.verify()["lines"] == 40


def _write_some(home):
    os.environ["CLOAKROOM_HOME"] = home
    import importlib
    from cloakroom import config, events
    importlib.reload(config)
    importlib.reload(events)
    for _ in range(10):
        events.record("pre", {"tool_name": "Bash"}, "pass", [], 1.0)


def test_controls_and_bypass_are_logged(monkeypatch, capsys):
    from cloakroom import events
    for prompt in ("cr: pause 5m", "cr: resume", "cr: allow", "cr! my key is " + KEY):
        run_hook("prompt", {"session_id": "s", "prompt": prompt}, monkeypatch, capsys)
    assert [e["action"] for e in events.read()][::-1] == ["pause on", "pause off", "allow armed", "sent anyway"]
    assert KEY not in "".join(_lines())


def test_fail_closed_and_dry_run_are_logged(monkeypatch, capsys):
    from cloakroom import events, hook
    monkeypatch.setattr(hook, "on_pre", lambda data: 1 / 0)
    monkeypatch.setitem(hook.HANDLERS, "pre", hook.on_pre)
    out = run_hook("pre", {"session_id": "s", "tool_name": "WebFetch", "tool_input": {}}, monkeypatch, capsys)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny" and events.read(1)[0]["action"] == "fail-closed"
    monkeypatch.undo()
    monkeypatch.setenv("CLOAKROOM_HOME", events.config.HOME.as_posix())
    monkeypatch.setenv("CLOAKROOM_MODE", "rules")
    monkeypatch.setenv("CLOAKROOM_DRY_RUN", "1")
    run_hook("prompt", {"session_id": "s", "prompt": f"token {KEY}"}, monkeypatch, capsys)
    assert events.read(1)[0]["action"] == "dry-run"


def test_session_start_line(monkeypatch, capsys, tmp_path):
    from cloakroom import __version__, events
    plugin = tmp_path / "plugin"
    (plugin / "hooks").mkdir(parents=True)
    (plugin / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [], "PreToolUse": []}}))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    run_hook("start", {"session_id": "s"}, monkeypatch, capsys)
    ev = events.read(1)[0]
    assert ev["action"] == "session-start" and ev["version"] == __version__ and ev["mode"] == "rules"
    assert ev["hooks"] == ["PreToolUse", "SessionStart"]


# --- 2b report ------------------------------------------------------------------------------------------------

def _some_events(monkeypatch, capsys):
    run_hook("post", {"session_id": "r1", "tool_name": "Read", "tool_input": {"file_path": os.path.expanduser("~/code/app/.env")},
                      "tool_response": {"file": {"content": f"TOKEN={KEY}\nmail jo.smith@fernhill.co"}}},
             monkeypatch, capsys)
    run_hook("prompt", {"session_id": "r1", "prompt": f"here {KEY}"}, monkeypatch, capsys)
    run_hook("pre", {"session_id": "r1", "tool_name": "Bash", "tool_input": {"command": "ls"}}, monkeypatch, capsys)


def test_report_counts_without_values(monkeypatch, capsys):
    from cloakroom import report
    _some_events(monkeypatch, capsys)
    text = report.export("csv")
    assert text.startswith("# Cloakroom report:") and "does not show what Cloakroom missed" in text
    assert "cr!" in text and "compliance" in text and "log chain intact" in text
    body = [line for line in text.splitlines() if not line.startswith("#")]
    rows = list(csv.DictReader(io.StringIO("\n".join(body))))
    got = {(r["action"], r["kind"], r["path_class"]): int(r["count"]) for r in rows}
    assert got[("masked", "SECRET", ".env in code")] == 1 and got[("masked", "EMAIL", ".env in code")] == 1
    assert got[("denied", "SECRET", "")] == 1
    assert not any(r["action"] == "pass" for r in rows)
    for bad in (KEY, "PII_", "jo.smith", "/app/"):
        assert bad not in text
    data = json.loads(report.export("json"))
    assert data["chain"]["ok"] and data["rows"] and "about" in data and "PII_" not in json.dumps(data)


def test_report_since_and_path_class():
    from cloakroom import events, report
    events.record("post", {"tool_name": "Read", "session_id": "x"}, "masked", [("", "SECRET")], 1.0, "/tmp/a/b.py")
    assert report.rows(since="2999-01-01") == []
    assert report.path_class(os.path.expanduser("~/code/app/PII_PERSON_1/x.PY")) == ".py in code"
    assert report.path_class("grep", "Bash") == "grep" and report.path_class("") == ""
    assert report.path_class("jane_doe_cv.pdf") == ".pdf"
    assert report.path_class("/etc/hosts") == "no extension in etc"


def test_report_cli_and_verify(monkeypatch, capsys):
    from cloakroom import cli, events
    _some_events(monkeypatch, capsys)
    assert cli.main(["report", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"]
    assert cli.main(["report", "--verify"]) == 0
    out = capsys.readouterr().out
    assert "intact" in out and events.head() in out
    path = events.months()[-1]
    path.write_text(path.read_text().replace('"r1"', '"r2"', 1))
    assert cli.main(["report", "--verify"]) == 1 and "BROKEN" in capsys.readouterr().out


def test_dashboard_export_uses_the_report():
    from cloakroom import config, daemon
    src = (config.Path(daemon.__file__).parent / "dashboard.html").read_text()
    assert 'href="/api/report?format=csv"' in src and "report.export(fmt)" in config.Path(daemon.__file__).read_text()


# --- 4d clean-history -----------------------------------------------------------------------------------------

@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    root = tmp_path / "claude"
    proj = root / "projects" / "-Users-x-code-app"
    proj.mkdir(parents=True)
    old = time.time() - 3600
    t1 = proj / "aaa.jsonl"
    t1.write_text(json.dumps({"type": "user", "message": {"content": f"use {KEY} please"}}) + "\n"
                  + json.dumps({"type": "assistant", "message": {"content": "ok"}}) + "\n")
    busy = proj / "current.jsonl"
    busy.write_text(json.dumps({"message": {"content": f"key {KEY}"}}) + "\n")
    hist = root / "history.jsonl"
    hist.write_text(json.dumps({"display": f"password: Hunter2Hunter2!x"}) + "\n")
    for p in (t1, hist):
        os.utime(p, (old, old))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    return root


def test_clean_history_first_run_is_dry(claude_home):
    from cloakroom import history
    said = []
    before = (claude_home / "projects/-Users-x-code-app/aaa.jsonl").read_text()
    assert history.run(yes=True, say=said.append) == 0  # --yes, but the first run is a dry run anyway
    text = "\n".join(said)
    assert "-Users-x-code-app: 1" in text and "Dry run" in text and "skipped 1 file(s) changed" in text
    assert (claude_home / "projects/-Users-x-code-app/aaa.jsonl").read_text() == before
    assert KEY not in text


def test_clean_history_rewrites_with_backup(claude_home):
    from cloakroom import config, history
    history.run(say=lambda *_: None)  # first, dry
    assert history.run(ask=lambda q: "n", say=lambda *_: None) == 0
    assert KEY in (claude_home / "projects/-Users-x-code-app/aaa.jsonl").read_text()
    said = []
    history.run(ask=lambda q: "y", say=said.append)
    t1 = claude_home / "projects/-Users-x-code-app/aaa.jsonl"
    lines = [json.loads(line) for line in t1.read_text().splitlines()]  # still valid JSON lines
    assert KEY not in t1.read_text() and "[SECRET removed by Cloakroom]" in lines[0]["message"]["content"]
    assert lines[1] == {"type": "assistant", "message": {"content": "ok"}}
    assert KEY in (claude_home / "projects/-Users-x-code-app/current.jsonl").read_text()  # busy file untouched
    backups = list((config.HOME / "history-backup").rglob("aaa.jsonl"))
    assert len(backups) == 1 and KEY in backups[0].read_text() and "Originals:" in said[-1]


def test_clean_history_cli(claude_home, capsys):
    from cloakroom import cli
    assert cli.main(["clean-history", "--dry-run"]) == 0
    assert "key(s) or password(s)" in capsys.readouterr().out


# --- 4e (test only): real values in MCP tool inputs are denied ------------------------------------------------

def test_mcp_input_with_real_value_denied(monkeypatch, capsys):
    out = run_hook("pre", {"session_id": "m", "tool_name": "mcp__github__create_issue",
                           "tool_input": {"title": "bug", "body": f"token {KEY}"}}, monkeypatch, capsys)
    h = out["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny" and KEY not in json.dumps(out)
    run_hook("post", {"session_id": "m", "tool_name": "Read",
                      "tool_response": {"file": {"content": f"token = {KEY}"}}}, monkeypatch, capsys)
    assert run_hook("pre", {"session_id": "m", "tool_name": "mcp__github__create_issue",
                            "tool_input": {"body": "token PII_SECRET_1"}}, monkeypatch, capsys) is None
