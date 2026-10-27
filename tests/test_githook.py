"""0.7.1: cloakroom install git-hook. Your own commits and pushes get the agent commit hold's check (keys and
passwords in added lines), with no agent running. Real git, a temp repo, the hook calling this checkout's code."""
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

import pytest

from cloakroom import cli

KEY = "ghp_" + "Q7aZ0kLm3Np9Rt2Vw5Xy8Bc1Df4Gh6Jk0LmPq"  # a GitHub-token shape, not a real one


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], check=check, capture_output=True, text=True)


def shim(folder):
    """A cloakroom program for the hook to run: this checkout, this Python. Written with LF only: Git for Windows'
    sh would read a CR as part of the line."""
    folder.mkdir(parents=True, exist_ok=True)
    prog = folder / "cloakroom"
    prog.write_text(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" -m cloakroom "$@"\n', newline="\n")
    prog.chmod(0o755)
    return prog


@pytest.fixture
def repo(tmp_path, monkeypatch):
    prog = shim(tmp_path / "my bin")  # a space in the path, like C:\Users\Jane Doe
    from cloakroom import agents
    monkeypatch.setattr(agents, "_program", lambda: str(prog))
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    for key, value in (("user.name", "t"), ("user.email", "t@example.com"), ("core.autocrlf", "false")):
        git(r, "config", key, value)
    (r / "README.md").write_text("hello\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "first")
    monkeypatch.chdir(r)
    return r


def test_commit_with_a_key_is_stopped_and_a_clean_one_passes(repo, capsys):
    assert cli.main(["install", "git-hook"]) == 0
    assert "added" in capsys.readouterr().out
    assert b"\r" not in (repo / ".git" / "hooks" / "pre-commit").read_bytes()  # LF: Git for Windows' sh too
    (repo / "app.py").write_text(f"TOKEN = '{KEY}'\n")
    git(repo, "add", "app.py")
    r = git(repo, "commit", "-m", "key", check=False)
    assert r.returncode != 0 and "stopped this commit" in r.stderr and "app.py" in r.stderr
    assert "--no-verify" in r.stderr and KEY not in r.stderr
    (repo / "app.py").write_text("TOKEN = os.environ['TOKEN']\n")
    git(repo, "add", "app.py")
    assert git(repo, "commit", "-m", "clean", check=False).returncode == 0
    (repo / "b.py").write_text(f"K = '{KEY}'\n")
    (repo / "README.md").write_text("hello again\n")
    assert git(repo, "commit", "-am", "x", check=False).returncode == 0  # b.py untracked: not in the commit
    (repo / "README.md").write_text(f"key {KEY}\n")
    assert git(repo, "commit", "-am", "x", check=False).returncode != 0  # commit -a: what it stages is checked
    git(repo, "add", "b.py")
    assert git(repo, "commit", "--no-verify", "-m", "anyway", check=False).returncode == 0  # git's own bypass


def test_push_with_a_key_is_stopped(repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "main")
    assert cli.main(["install", "git-hook"]) == 0
    (repo / "app.py").write_text(f"TOKEN = '{KEY}'\n")
    git(repo, "add", "app.py")
    git(repo, "commit", "--no-verify", "-q", "-m", "key")
    r = git(repo, "push", "origin", "main", check=False)
    assert r.returncode != 0 and "stopped this push" in r.stderr


def _stopped(repo):
    (repo / "app.py").write_text(f"TOKEN = '{KEY}'\n")
    git(repo, "add", "app.py")
    return git(repo, "commit", "-m", "key", check=False)


def test_the_installed_program_runs_from_the_hook(repo, monkeypatch):
    """The real cloakroom(.exe) next to this Python (on Windows a backslash path under Scripts)."""
    prog = os.path.join(sysconfig.get_path("scripts"), "cloakroom.exe" if sys.platform == "win32" else "cloakroom")
    if not os.path.isfile(prog):
        # in CI the package is always installed, so a missing program is a broken build, not a reason to skip
        (pytest.fail if os.environ.get("CI") else pytest.skip)("cloakroom isn't installed next to this Python")
    from cloakroom import agents
    monkeypatch.setattr(agents, "_program", lambda: prog)
    assert cli.main(["install", "git-hook"]) == 0
    assert "\\" not in (repo / ".git" / "hooks" / "pre-commit").read_text()
    r = _stopped(repo)
    assert r.returncode != 0 and "stopped this commit" in r.stderr


def test_a_missing_program_lets_the_commit_through_then_path_is_used(repo, tmp_path, monkeypatch):
    from cloakroom import agents
    monkeypatch.setattr(agents, "_program", lambda: str(tmp_path / "gone" / "cloakroom"))
    monkeypatch.setenv("PATH", os.pathsep.join(d for d in os.environ["PATH"].split(os.pathsep)
                                                if d and not shutil.which("cloakroom", path=d)))
    assert cli.main(["install", "git-hook"]) == 0
    r = _stopped(repo)
    assert r.returncode == 0 and "cloakroom: not installed, so this commit was not checked" in r.stderr
    git(repo, "reset", "-q", "--soft", "HEAD~1")
    monkeypatch.setenv("PATH", str(tmp_path / "my bin") + os.pathsep + os.environ["PATH"])  # found on PATH instead
    r = git(repo, "commit", "-m", "key", check=False)
    assert r.returncode != 0 and "stopped this commit" in r.stderr


def test_a_windows_path_is_written_with_forward_slashes(monkeypatch):
    from cloakroom import githook
    monkeypatch.setattr(sys, "platform", "win32")
    s = githook._script(r"C:\Users\Jane O'Neil\.local\bin\cloakroom.exe", "commit")
    assert "p='C:/Users/Jane O'\"'\"'Neil/.local/bin/cloakroom.exe'\n" in s and "\\" not in s and "\r" not in s


def test_always_mask_values_count(repo, monkeypatch):
    from cloakroom import config, hook
    monkeypatch.setattr(config, "load", lambda: {**config.DEFAULTS, "always": [("hunter2-long-secret", "PASSWORD")]})
    (repo / "x.txt").write_text("pw: hunter2-long-secret\n")
    git(repo, "add", "x.txt")
    found = hook.git_added(str(repo), [["diff", "--cached"]], None, config.load(), time.monotonic() + 10)
    assert found and found[0][0] == "x.txt" and found[0][2] == "PASSWORD"


def test_existing_hook_is_left_alone_and_install_runs_twice(repo, capsys):
    hooks = repo / ".git" / "hooks"
    (hooks / "pre-commit").write_text("#!/bin/sh\necho mine\n")
    assert cli.main(["install", "git-hook"]) == 1  # one hook skipped
    out = capsys.readouterr().out
    assert "another tool's hook" in out and "cloakroom git-check commit || exit 1" in out
    assert (hooks / "pre-commit").read_text() == "#!/bin/sh\necho mine\n"
    assert "cloakroom git hook" in (hooks / "pre-push").read_text()
    assert cli.main(["install", "git-hook"]) == 1  # again: same result, nothing duplicated
    assert cli.main(["uninstall", "git-hook"]) == 0
    assert (hooks / "pre-commit").read_text() == "#!/bin/sh\necho mine\n" and not (hooks / "pre-push").exists()


def test_core_hooks_path_is_respected_and_uninstall_round_trips(repo, capsys):
    git(repo, "config", "core.hooksPath", ".githooks")
    assert cli.main(["install", "git-hook"]) == 0
    assert (repo / ".githooks" / "pre-commit").exists() and not (repo / ".git" / "hooks" / "pre-commit").exists()
    assert os.access(repo / ".githooks" / "pre-commit", os.X_OK)
    (repo / "app.py").write_text(f"TOKEN = '{KEY}'\n")
    git(repo, "add", "app.py")
    assert git(repo, "commit", "-m", "key", check=False).returncode != 0
    assert cli.main(["uninstall", "git-hook"]) == 0
    assert not (repo / ".githooks" / "pre-commit").exists() and not (repo / ".githooks" / "pre-push").exists()
    assert git(repo, "commit", "-m", "key", check=False).returncode == 0
    assert cli.main(["uninstall", "git-hook"]) == 0  # nothing left to remove: fine


def test_not_a_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["install", "git-hook"]) == 1
    assert "not in a git repo" in capsys.readouterr().err


def _events():
    from cloakroom import events
    return list(reversed(events.read()))


def test_a_stopped_commit_is_logged_as_held_without_values(repo):
    assert cli.main(["install", "git-hook"]) == 0
    (repo / "app.py").write_text(f"TOKEN = '{KEY}'\n")
    git(repo, "add", "app.py")
    assert git(repo, "commit", "-m", "key", check=False).returncode != 0
    from cloakroom import events
    ev = [e for e in _events() if e["action"] == "held"]
    assert len(ev) == 1 and ev[0]["tool"] == "git hook" and ev[0]["session"] == "git-hook"
    assert ev[0]["kinds"] and ev[0]["target"] == "" and ev[0]["placeholders"] == []
    assert KEY not in json.dumps(_events()) and events.verify()["ok"]


@pytest.mark.parametrize("kind", ["commit", "push"])
def test_too_large_is_logged_as_skipped_and_told(repo, monkeypatch, capsys, kind):
    from cloakroom import githook, hook
    monkeypatch.setattr(hook, "GIT_MAX_TEXT", 8)
    (repo / "app.py").write_text(f"TOKEN = '{KEY}'\n")
    git(repo, "add", "app.py")
    if kind == "push":
        git(repo, "commit", "--no-verify", "-q", "-m", "key")
        sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        stdin = [f"refs/heads/main {sha} refs/heads/main {githook.ZERO}\n"]
    else:
        stdin = None
    assert githook.check(kind, stdin) == 0  # fails open
    assert capsys.readouterr().err.strip() == f"Cloakroom didn't check this git {kind}: it was larger than 8 bytes"
    ev = [e for e in _events() if e["event"] == "skipped"]
    assert [(e["reason"], e["check"], e["limit"]) for e in ev] == [("too-large", f"git {kind} (git hook)", 8)]
    assert ev[0]["size"] > 8 and KEY not in json.dumps(ev)


def test_too_slow_and_git_error_are_logged_as_skipped(repo, monkeypatch, capsys):
    from cloakroom import githook
    monkeypatch.setattr(githook, "SECONDS", 0.0)
    assert githook.check("commit") == 0
    assert "Cloakroom didn't check this git commit: it took longer than" in capsys.readouterr().err
    monkeypatch.setattr(githook, "SECONDS", 10.0)
    monkeypatch.chdir(repo.parent)  # not a repo: git errors
    assert githook.check("commit") == 0
    assert capsys.readouterr().err.strip() == "Cloakroom didn't check this git commit: git returned an error"
    assert [(e["reason"], e["check"]) for e in _events() if e["event"] == "skipped"] == [
        ("too-slow", "git commit (git hook)"), ("git-error", "git commit (git hook)")]
