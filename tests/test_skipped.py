"""0.7.1: every fail-open path is logged as a "skipped" event (no values), told to the user once per session per
reason, and counted in `cloakroom report`. Synthetic data only."""
import io
import json
import subprocess
import sys

import pytest

KEY = "ghp_" + "Q7aZ0kLm3Np9Rt2Vw5Xy8Bc1Df4Gh6Jk0LmPq"  # a GitHub-token shape, not a real one


def hook_call(event, data, monkeypatch, capsys, agent="claude"):
    from cloakroom import hookentry
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(data).encode())))
    assert hookentry.run(event, agent) == 0
    return json.loads(capsys.readouterr().out.strip() or "null") or None


def bash(command, cwd, monkeypatch, capsys, session="s1", agent="claude"):
    return hook_call("pre", {"session_id": session, "tool_name": "Bash", "cwd": str(cwd),
                             "tool_input": {"command": command}}, monkeypatch, capsys, agent)


def skips():
    from cloakroom import events
    return [e for e in reversed(events.read()) if e["event"] == "skipped"]


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "README.md").write_text("hello\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "first")
    return r


def test_wording_matches_the_limits():
    from cloakroom import events, hook
    assert events.skip_why({"reason": "too-large", "limit": hook.GIT_MAX_TEXT}) == "it was larger than 4 MB"
    assert events.skip_why({"reason": "too-large", "limit": hook.PRIME_MAX_BYTES}) == "it was larger than 512 KB"
    assert events.skip_why({"reason": "too-slow", "limit": hook.GIT_SECONDS}) == "it took longer than 2 s"
    assert events.skip_why({"reason": "too-slow"}) == "it took longer than its limit"


def test_push_over_the_size_cap_runs_is_logged_and_told_once(repo, tmp_path, monkeypatch, capsys):
    from cloakroom import hook
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    (repo / "app.py").write_text(f"TOKEN = '{KEY}'\n" + "x = 1\n" * 300)
    git(repo, "add", "app.py")
    git(repo, "commit", "-q", "-m", "big")
    monkeypatch.setattr(hook, "GIT_MAX_TEXT", 1024)
    out = bash("git push", repo, monkeypatch, capsys)
    assert out == {"systemMessage": "Cloakroom didn't check this git push: it was larger than 1 KB"}  # not held
    assert bash("git push", repo, monkeypatch, capsys) is None  # once per session
    assert bash("git push", repo, monkeypatch, capsys, session="s2")["systemMessage"].startswith("Cloakroom didn't")
    ev = skips()[0]
    assert {k: ev[k] for k in ("event", "action", "tool", "reason", "check", "limit")} == {
        "event": "skipped", "action": "skipped", "tool": "Bash", "reason": "too-large", "check": "git push",
        "limit": 1024}
    assert ev["size"] > 1024 and ev["target"] == "" and ev["kinds"] == {} and KEY not in json.dumps(skips())
    from cloakroom import events
    assert events.verify()["ok"]  # the extra lines keep the chain


def test_slow_git_on_commit_is_logged_with_its_time(repo, monkeypatch, capsys):
    from cloakroom import hook
    (repo / "app.py").write_text(f"TOKEN = '{KEY}'\n")
    git(repo, "add", "app.py")
    monkeypatch.setattr(hook, "GIT_SECONDS", 0.0)
    out = bash("git commit -m x", repo, monkeypatch, capsys)
    assert "hookSpecificOutput" not in out and "this git commit: it took longer" in out["systemMessage"]
    ev = skips()[0]
    assert ev["reason"] == "too-slow" and ev["check"] == "git commit" and ev["limit"] == 0.0 and ev["ms"] >= 0


def test_a_new_file_over_the_cap_is_skipped_in_the_commit_scan(repo, monkeypatch, capsys):
    from cloakroom import hook
    monkeypatch.setattr(hook, "PRIME_MAX_BYTES", 16)  # untracked files are read up to 4x this
    (repo / "big.py").write_text(f"TOKEN = '{KEY}'\n" * 3)
    out = bash("git add -A && git commit -m x", repo, monkeypatch, capsys)
    assert out == {"systemMessage": "Cloakroom didn't check a new file in this git commit: it was larger than 64 bytes"}
    assert skips()[0]["check"] == "new file in a git commit" and skips()[0]["size"] > 64


def test_slow_git_on_a_write_holding_a_key_is_told(repo, monkeypatch, capsys):
    real = subprocess.run

    def slow(argv, *a, **k):
        if "check-ignore" in argv:
            raise subprocess.TimeoutExpired(argv, 1)
        return real(argv, *a, **k)
    monkeypatch.setattr(subprocess, "run", slow)
    out = hook_call("pre", {"session_id": "s1", "tool_name": "Write", "cwd": str(repo), "tool_input": {
        "file_path": str(repo / "app.py"), "content": f"TOKEN = '{KEY}'\n"}}, monkeypatch, capsys)
    assert out == {"systemMessage": "Cloakroom didn't check whether git would commit this write, which holds a key: "
                                    "it took longer than 1 s"}
    assert skips()[0]["check"] == "git check on a write holding a key"


def test_a_file_too_big_to_pre_read_is_logged_not_told(tmp_path, monkeypatch, capsys):
    from cloakroom import hook
    monkeypatch.setattr(hook, "PRIME_MAX_BYTES", 100)
    (tmp_path / "big.log").write_text("line\n" * 50)
    (tmp_path / "small.log").write_text("line\n")
    assert bash("cat small.log", tmp_path, monkeypatch, capsys) is None
    assert bash("cat big.log", tmp_path, monkeypatch, capsys) is None  # its output is still masked: nothing to tell
    ev = skips()[0]
    assert (ev["check"], ev["reason"], ev["size"], ev["limit"]) == ("pre-read of a file a command names",
                                                                    "too-large", 250, 100)


def test_prompt_on_rules_only_is_logged_with_the_warning_unchanged(monkeypatch, capsys):
    from cloakroom import client
    from cloakroom.hookentry import prompt_warning

    def down(*a, **k):
        raise client.Unavailable("cloakroom service is starting")
    monkeypatch.setattr(client, "mask", down)
    out = hook_call("prompt", {"session_id": "s1", "prompt": "hello there"}, monkeypatch, capsys)
    assert out == prompt_warning("cloakroom service is starting; checked with rules only")  # as before 0.7.1
    assert [(e["reason"], e["check"], e["tool"]) for e in skips()] == [("model", "model check", "")]


def test_rules_only_while_the_model_downloads_is_logged_not_repeated(monkeypatch, capsys):
    from cloakroom import config
    monkeypatch.delenv("CLOAKROOM_MODE")
    config.private_dir()
    (config.HOME / "bootstrap.state").write_text("model")
    out = hook_call("post", {"session_id": "s1", "tool_name": "mcp__x__y", "tool_input": {},
                             "tool_response": "nothing to see"}, monkeypatch, capsys)
    assert out is None  # the install note already tells; a local tool call isn't counted
    hook_call("pre", {"session_id": "s1", "tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}},
              monkeypatch, capsys)
    assert [e["reason"] for e in skips()] == ["model"]


def test_codex_gets_the_note_too(repo, monkeypatch, capsys):
    from cloakroom import hook
    (repo / "app.py").write_text(f"TOKEN = '{KEY}'\n")
    git(repo, "add", "app.py")
    monkeypatch.setattr(hook, "GIT_MAX_TEXT", 8)
    out = bash("git commit -m x", repo, monkeypatch, capsys, agent="codex")
    assert out == {"systemMessage": "Cloakroom didn't check this git commit: it was larger than 8 bytes"}


def test_report_has_a_checks_skipped_section(repo, monkeypatch, capsys):
    from cloakroom import hook, report
    (repo / "app.py").write_text(f"TOKEN = '{KEY}'\n")
    git(repo, "add", "app.py")
    monkeypatch.setattr(hook, "GIT_MAX_TEXT", 8)
    bash("git commit -m x", repo, monkeypatch, capsys)
    bash("git commit -m x", repo, monkeypatch, capsys, session="s2")
    monkeypatch.setattr(hook, "GIT_MAX_TEXT", 4 * 1024 * 1024)
    monkeypatch.setattr(hook, "GIT_SECONDS", 0.0)
    bash("git commit -m x", repo, monkeypatch, capsys)
    text = report.export("csv")
    assert "# Checks skipped: 3\n#   2 x git commit: it was larger than 8 bytes\n" \
           "#   1 x git commit: it took longer than its limit\n" in text
    assert "skipped" not in [r["action"] for r in report.rows()] and KEY not in text
    data = json.loads(report.export("json"))
    assert data["skipped"][0] == {"check": "git commit", "reason": "too-large", "why": "it was larger than 8 bytes",
                                  "count": 2}
    assert "Checks skipped: 0" in (report.export("csv", since="2999-01-01"))


def test_dashboard_leaves_skips_out_of_latency_and_counts_them():
    from pathlib import Path

    from cloakroom import dashboard
    src = Path(dashboard.__file__).with_name("dashboard.html").read_text()
    assert src.count('e.event !== "skipped"') == 2 and "not fully checked" in src
