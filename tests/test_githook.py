"""0.7.1: cloakroom install git-hook. Your own commits and pushes get the agent commit hold's check (keys and
passwords in added lines), with no agent running. Real git, a temp repo, the hook calling this checkout's code."""
import os
import time
import subprocess
import sys

import pytest

from cloakroom import cli

KEY = "ghp_" + "Q7aZ0kLm3Np9Rt2Vw5Xy8Bc1Df4Gh6Jk0LmPq"  # a GitHub-token shape, not a real one
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="sh hooks under Git for Windows: not tested yet")


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          check=check, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    prog = tmp_path / "bin" / "cloakroom"  # the program the hook runs: this checkout, this Python
    prog.parent.mkdir()
    prog.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m cloakroom "$@"\n')
    prog.chmod(0o755)
    from cloakroom import agents
    monkeypatch.setattr(agents, "_program", lambda: str(prog))
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "README.md").write_text("hello\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "first")
    monkeypatch.chdir(r)
    return r


def test_commit_with_a_key_is_stopped_and_a_clean_one_passes(repo, capsys):
    assert cli.main(["install", "git-hook"]) == 0
    assert "added" in capsys.readouterr().out
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
