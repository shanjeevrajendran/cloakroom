"""cloakroom scan, and the post-checkout / post-merge warning when a trusted repo's agent config gains or changes a
command. Fixture configs run echo only: the worm's shape, never its code."""
import json
import subprocess

from cloakroom import cli, configscan
from test_githook import git, repo  # noqa: F401 - the same temp repo and cloakroom shim

WORM_SETTINGS = {"hooks": {"SessionStart": [{"matcher": "*", "hooks": [
    {"type": "command", "command": "echo .vscode/setup.mjs"}]}]}}
WORM_TASKS = """{
  // VS Code allows comments and trailing commas here
  "version": "2.0.0",
  "tasks": [
    {"label": "Environment Setup", "type": "shell", "command": "echo .claude/setup.mjs",
     "runOptions": {"runOn": "folderOpen"}},
  ],
}
"""
BENIGN = {"hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "echo lint"}]}]}}


def _write(root, path, text):
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text if isinstance(text, str) else json.dumps(text, indent=2) + "\n")


def test_scan_flags_the_worm_shape(tmp_path, capsys):
    _write(tmp_path, ".claude/settings.json", WORM_SETTINGS)
    _write(tmp_path, ".vscode/tasks.json", WORM_TASKS)
    assert cli.main(["scan", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "WORM SHAPE" in out and ".claude/settings.json:9" in out and ".vscode/tasks.json:5" in out
    assert "runs on folder open" in out


def test_scan_lists_every_agent_config(tmp_path, capsys):
    _write(tmp_path, ".claude/settings.json", BENIGN)
    _write(tmp_path, ".mcp.json", {"mcpServers": {"files": {"command": "echo", "args": ["mcp"]},
                                                  "web": {"type": "http", "url": "https://example.com/mcp"}}})
    _write(tmp_path, ".gemini/settings.json", {"mcpServers": {"g": {"command": "echo gemini"}}})
    _write(tmp_path, ".codex/config.toml", '[mcp_servers.docs]\ncommand = "echo"\nargs = ["codex"]\n')
    _write(tmp_path, ".codex/hooks.json", {"hooks": {"SessionStart": [{"hooks": [{"command": "echo codex"}]}]}})
    assert cli.main(["scan", str(tmp_path)]) == 0  # nothing worm-shaped
    out = capsys.readouterr().out
    for want in ("PostToolUse hook: echo lint", "MCP server files: echo mcp", "MCP server web: https://example.com/mcp",
                 "MCP server g: echo gemini", '.codex/config.toml:2  mcp_servers.docs command: "echo"',
                 "SessionStart hook: echo codex"):
        assert want in out, want


def test_scan_of_an_empty_folder(tmp_path, capsys):
    assert cli.main(["scan", str(tmp_path)]) == 0
    assert "No agent config commands" in capsys.readouterr().out


def test_unreadable_json_is_reported_not_skipped():
    assert configscan.commands(".mcp.json", "{not json") == [("unreadable JSON", "", 1)]


def _guarded(repo):  # noqa: F811
    _write(repo, ".claude/settings.json", BENIGN)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "config")
    assert cli.main(["install", "git-hook"]) == 0
    assert (repo / ".git" / "hooks" / "post-checkout").exists() and (repo / ".git" / "hooks" / "post-merge").exists()


def test_a_changed_command_is_reported_with_file_and_line_after_checkout(repo):  # noqa: F811
    _guarded(repo)
    git(repo, "checkout", "-q", "-b", "other")
    s = json.loads(json.dumps(BENIGN))
    s["hooks"]["PostToolUse"][0]["hooks"][0]["command"] = "echo lint --fix"
    s["hooks"]["SessionStart"] = [{"hooks": [{"type": "command", "command": "echo hi"}]}]
    _write(repo, ".claude/settings.json", s)
    git(repo, "commit", "-qam", "change")
    r = git(repo, "checkout", "main")
    assert r.returncode == 0 and "changed PostToolUse hook: echo lint\n" in r.stderr  # back to the old text: a change
    assert "SessionStart" not in r.stderr  # a removed command is not news
    r = git(repo, "checkout", "other")
    assert r.returncode == 0
    assert ".claude/settings.json:9 changed PostToolUse hook: echo lint --fix" in r.stderr
    assert "new SessionStart hook: echo hi" in r.stderr and "nothing was blocked" in r.stderr


def test_a_benign_unchanged_config_is_silent(repo):  # noqa: F811
    _guarded(repo)
    git(repo, "checkout", "-q", "-b", "other")
    (repo / "README.md").write_text("changed\n")
    git(repo, "commit", "-qam", "readme")
    for branch in ("main", "other"):
        r = git(repo, "checkout", branch)
        assert r.returncode == 0 and r.stderr.strip() == f"Switched to branch '{branch}'"


def test_a_pull_that_plants_the_worm_shape_warns_and_never_blocks(repo, tmp_path):  # noqa: F811
    _guarded(repo)
    upstream = tmp_path / "upstream"
    subprocess.run(["git", "clone", "-q", str(repo), str(upstream)], check=True)
    for key, value in (("user.name", "t"), ("user.email", "t@example.com")):
        git(upstream, "config", key, value)
    _write(upstream, ".claude/settings.json", WORM_SETTINGS)
    _write(upstream, ".vscode/tasks.json", WORM_TASKS)
    git(upstream, "add", ".")
    git(upstream, "commit", "-q", "-m", "chore: update dependencies")
    r = git(repo, "pull", "-q", "--no-rebase", str(upstream), "main")
    assert r.returncode == 0  # fast-forward done: warned, not stopped
    assert "this merge added or changed commands" in r.stderr and "WORM SHAPE" in r.stderr
    assert ".vscode/tasks.json:5 new task 'Environment Setup' (runs on folder open): echo .claude/setup.mjs" in r.stderr
    assert json.loads((repo / ".claude/settings.json").read_text()) == WORM_SETTINGS  # never edited


def test_the_post_hook_never_fails_the_checkout(repo, monkeypatch):  # noqa: F811
    def boom(*a):
        raise RuntimeError("x")
    monkeypatch.setattr(configscan, "collect", boom)
    assert configscan.check("checkout", ["a" * 40, "b" * 40, "1"]) == 0
    assert configscan.check("checkout", ["a" * 40, "b" * 40, "0"]) == 0  # single-file checkout: not compared
