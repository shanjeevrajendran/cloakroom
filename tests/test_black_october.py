"""Two misfires the Black October bench found (bench/black-october/). Synthetic values only."""
import json

import pytest
from test_git_hold import KEY, bash, decision, git
from test_shell_and_hooks import run_hook


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "README.md").write_text("hello\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "first")
    return r

GATEWAY = "sk-" + "Tq7Wm2Rv9Lx4Kp8Nz3Hc6Bd1Fy5Gs0Ja"  # split so secret scanners don't flag this file
OPENCODE = json.dumps({"provider": {"litellm": {"options": {"baseURL": "https://agents.internal.example/v1",
                                                            "apiKey": GATEWAY}}}}, indent=2)


def read(path, content, monkeypatch, capsys, session="b"):
    out = run_hook("post", {"session_id": session, "tool_name": "Read", "tool_input": {"file_path": path},
                            "tool_response": {"file": {"content": content}}}, monkeypatch, capsys)
    return out["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]


def test_short_known_value_in_plain_text_masks_not_withholds(monkeypatch, capsys):
    """The .env's database user ("litellm", 7 characters) is a known value; the same word as a plain JSON key in the
    next file is not an encoded copy of it, so that file is masked, not withheld whole."""
    read("/p/.env", "DATABASE_URL=postgresql://litellm:Q5DVB5Ugf9e7u6uYCT22@db.example:5432/app\n",
         monkeypatch, capsys)
    body = read("/p/opencode.json", OPENCODE, monkeypatch, capsys)
    assert "withheld" not in body and GATEWAY not in body and "PII_SECRET_" in body


def test_short_known_value_spaced_out_still_withheld(monkeypatch, capsys):
    read("/p/.env", "DATABASE_URL=postgresql://litellm:Q5DVB5Ugf9e7u6uYCT22@db.example:5432/app\n",
         monkeypatch, capsys)
    out = run_hook("post", {"session_id": "b", "tool_name": "Bash", "tool_input": {"command": "od -c x"},
                            "tool_response": {"stdout": "0000000   l   i   t   e   l   l   m\n", "stderr": ""}},
                   monkeypatch, capsys)
    assert "withheld" in out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]


def test_commit_hold_names_only_files_the_add_stages(repo, monkeypatch, capsys):
    (repo / "creds.txt").write_text(f"TOKEN={KEY}\n")  # untracked, not ignored, not named by the add
    (repo / "README.md").write_text(f"hello {KEY}\n")  # tracked, changed, not staged
    (repo / "SETUP.md").write_text("# Setup\n")
    assert bash("git add SETUP.md && git commit -m setup", repo, monkeypatch, capsys) is None
    (repo / "SETUP.md").write_text(f"# Setup\n{KEY}\n")
    out = bash("git add SETUP.md && git commit -m setup", repo, monkeypatch, capsys)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "SETUP.md" in reason and "creds.txt" not in reason and "README.md" not in reason
    (repo / "SETUP.md").write_text("# Setup\n")
    assert decision(bash("git add *.txt && git commit -m c", repo, monkeypatch, capsys)) == "deny"  # a glob
    assert decision(bash("git add -u && git commit -m c", repo, monkeypatch, capsys)) == "deny"  # tracked README
    git(repo, "checkout", "README.md")
    assert bash("git add -u && git commit -m c", repo, monkeypatch, capsys) is None  # -u adds no new file
