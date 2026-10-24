"""Write-time check: a key or password restored (or typed) into a file git would commit is denied."""
import io
import json
import subprocess
import sys

import pytest
from conftest import tag

KEY = "ghp_" + "Q7aZ0kLm3Np9Rt2Vw5Xy8Bc1Df4Gh6Jk0LmPq"  # a GitHub-token shape, not a real one


def run_hook(event, data, monkeypatch, capsys):
    from cloakroom import hook
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    hook.main(event)
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    (r / ".gitignore").write_text("secrets/\n")
    return r


def _mask_key(monkeypatch, capsys, session="c"):
    post = run_hook("post", {"session_id": session, "tool_name": "Read",
                             "tool_response": {"file": {"content": f"token = {KEY}"}}}, monkeypatch, capsys)
    assert "PII_SECRET_1" in post["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]


def _write(path, content, cwd, monkeypatch, capsys, session="c", tool="Write"):
    content = tag(content, session)
    args = {"file_path": str(path), "content": content}
    if tool == "Edit":
        args = {"file_path": str(path), "old_string": "x", "new_string": content}
    return run_hook("pre", {"session_id": session, "tool_name": tool, "cwd": str(cwd), "tool_input": args},
                    monkeypatch, capsys)


def test_tracked_file_denied(repo, monkeypatch, capsys):
    _mask_key(monkeypatch, capsys)
    for tool in ("Write", "Edit"):
        out = _write(repo / "app" / "config.py", "TOKEN = 'PII_SECRET_1'\n", repo, monkeypatch, capsys, tool=tool)
        h = out["hookSpecificOutput"]
        assert h["permissionDecision"] == "deny" and "updatedInput" not in h
        reason = h["permissionDecisionReason"]
        assert "PII_SECRET_1" in reason and "app/config.py" in reason and "git would commit" in reason
        assert KEY not in json.dumps(out)
    from cloakroom import events
    ev = events.read(1)[0]
    assert ev["action"] == "deny" and ev["kinds"] == {"SECRET": 1} and KEY not in json.dumps(ev)


def test_ignored_file_restored(repo, monkeypatch, capsys):
    _mask_key(monkeypatch, capsys)
    out = _write(repo / "secrets" / "local.py", "TOKEN = 'PII_SECRET_1'\n", repo, monkeypatch, capsys)
    assert out["hookSpecificOutput"]["updatedInput"]["content"] == f"TOKEN = '{KEY}'\n"


def test_env_file_restored(repo, monkeypatch, capsys):
    _mask_key(monkeypatch, capsys)
    out = _write(repo / ".env.local", "TOKEN=PII_SECRET_1\n", repo, monkeypatch, capsys)
    assert out["hookSpecificOutput"]["updatedInput"]["content"] == f"TOKEN={KEY}\n"


def test_outside_repo_restored(tmp_path, monkeypatch, capsys):
    _mask_key(monkeypatch, capsys)
    plain = tmp_path / "plain"
    plain.mkdir()
    out = _write(plain / "config.py", "TOKEN = 'PII_SECRET_1'\n", plain, monkeypatch, capsys)
    assert out["hookSpecificOutput"]["updatedInput"]["content"] == f"TOKEN = '{KEY}'\n"


def test_key_typed_without_placeholder_denied(repo, monkeypatch, capsys):
    out = _write(repo / "deploy.py", f"TOKEN = '{KEY}'\n", repo, monkeypatch, capsys)
    h = out["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny" and "SECRET into deploy.py" in h["permissionDecisionReason"]
    assert KEY not in h["permissionDecisionReason"]


def test_example_key_and_pii_only_allowed(repo, monkeypatch, capsys):
    assert _write(repo / "README.md", "Set OPENAI_API_KEY=sk-your-key-here\n", repo, monkeypatch, capsys) is None
    run_hook("post", {"session_id": "c", "tool_name": "Read",
                      "tool_response": {"file": {"content": "mail jo.smith@fernhill.co"}}}, monkeypatch, capsys)
    out = _write(repo / "contacts.md", "owner: PII_EMAIL_1\n", repo, monkeypatch, capsys)
    assert out["hookSpecificOutput"]["updatedInput"]["content"] == "owner: jo.smith@fernhill.co\n"
    assert _write(repo / "c2.md", "owner: jo.smith@fernhill.co\n", repo, monkeypatch, capsys) is None


def test_allow_secret_paths_honoured(repo, monkeypatch, capsys):
    from cloakroom import config
    config.private_dir()
    (config.HOME / "config.json").write_text(json.dumps({"allow_secret_paths": ["tests/fixtures/*"]}))
    assert _write(repo / "tests" / "fixtures" / "k.txt", f"{KEY}\n", repo, monkeypatch, capsys) is None
    assert _write(repo / "src" / "k.txt", f"{KEY}\n", repo, monkeypatch, capsys)[
        "hookSpecificOutput"]["permissionDecision"] == "deny"


def test_check_ignore_failure_fails_open(repo, monkeypatch, capsys):
    import subprocess as sp
    from cloakroom import hook

    _mask_key(monkeypatch, capsys)
    assert hook._git_would_commit(str(repo / "config.py")) is True
    for err in (sp.TimeoutExpired("git", 1), FileNotFoundError("git")):  # slow git, no git at all
        def boom(*a, _e=err, **k):
            raise _e
        monkeypatch.setattr(sp, "run", boom)
        out = _write(repo / "config.py", "TOKEN = 'PII_SECRET_1'\n", repo, monkeypatch, capsys)
        assert out["hookSpecificOutput"]["updatedInput"]["content"] == f"TOKEN = '{KEY}'\n"


def test_bash_restore_redirect_into_tracked_file_denied(repo, monkeypatch, capsys):
    _mask_key(monkeypatch, capsys)
    deny = run_hook("pre", {"session_id": "c", "tool_name": "Bash", "cwd": str(repo),
                            "tool_input": {"command": tag("echo PII_SECRET_1 > token.txt", "c")}}, monkeypatch, capsys)
    assert deny["hookSpecificOutput"]["permissionDecision"] == "deny"
    ok = run_hook("pre", {"session_id": "c", "tool_name": "Bash", "cwd": str(repo),
                          "tool_input": {"command": tag("echo PII_SECRET_1 > .env", "c")}}, monkeypatch, capsys)
    assert KEY in ok["hookSpecificOutput"]["updatedInput"]["command"]


def test_dry_run_logs_would_deny(repo, monkeypatch, capsys):
    from cloakroom import config
    _mask_key(monkeypatch, capsys)
    monkeypatch.setenv("CLOAKROOM_DRY_RUN", "1")
    assert _write(repo / "config.py", "TOKEN = 'PII_SECRET_1'\n", repo, monkeypatch, capsys) is None
    log = (config.HOME / "dry-run.log").read_text()
    assert "would deny a commit-bound write" in log and "would restore" not in log
