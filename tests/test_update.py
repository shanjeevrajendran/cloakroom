"""0.8: cloakroom update (the program upgraded the way it was installed, extras kept, agent hooks refreshed where
installed) and doctor's version lines (the program, and Claude Code's plugin when it is there). No network: every
command `update` would run is recorded by a fake subprocess.run."""
import json
import subprocess

import pytest

from cloakroom import __version__, agents, cli, update

GIT_HOOK_HERE = update._git_hook_here  # runs git: the fixture swaps it out, as subprocess.run is faked there


class Dist:
    def __init__(self, version="0.7.1", installer=None, direct_url=None):
        self.version, self.files = version, {"INSTALLER": installer, "direct_url.json": direct_url}

    def read_text(self, name):
        return self.files.get(name)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Codex, Gemini CLI and Claude Code homes in tmp, a folder that is no git repo, no uv or pipx lookups."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))  # Cursor's home too, never the real one
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    monkeypatch.setattr(update.shutil, "which", lambda name: f"/opt/bin/{name}")
    monkeypatch.setattr(update, "extras", lambda: ["model", "torch", "ocr"])
    monkeypatch.setattr(update, "_git_hook_here", lambda: False)
    return tmp_path


def test_install_method_is_detected_and_unknown_ones_only_print(home):
    env = home / "env"
    env.mkdir()
    name, cmd, _ = update.route(str(env), Dist(installer="pip"), nt=False)
    assert name == "pip" and cmd[1:] == ["-m", "pip", "install", "-U", "cloakroom[model,torch,ocr]"]
    (env / "pipx_metadata.json").write_text("{}")
    assert update.route(str(env), Dist(installer="pip"), nt=False)[:2] == (
        "pipx", ["pipx", "install", "--force", "cloakroom[model,torch,ocr]"])
    (env / "uv-receipt.toml").write_text("[tool]\n")
    name, cmd, _ = update.route(str(env), Dist(installer="uv"), nt=False)
    assert name == "uv" and cmd[:4] == ["/opt/bin/uv", "tool", "install", "--force"]
    assert cmd[-1] == "cloakroom[model,torch,ocr]"  # no ==pin: the latest release
    # printed, never run: another installer, a source checkout, a dev version, Windows
    other = home / "other"
    other.mkdir()
    assert update.route(str(other), Dist(installer="conda"), nt=False)[0] is None
    assert update.route(str(env), Dist(direct_url='{"dir_info": {"editable": true}}'), nt=False)[:2] == (None, None)
    assert update.route(str(env), Dist(version="0.8.0.dev1"), nt=False)[0] is None
    name, cmd, why = update.route(str(env), Dist(installer="uv"), nt=True)
    assert name is None and cmd[1] == "tool" and "Windows" in why


def test_the_extras_that_are_installed_are_kept():
    have = {"onnxruntime", "tokenizers", "rapidocr"}
    found = update.extras(lambda m: object() if m in have else None)
    assert found == ["model", "ocr"] and update.spec(found) == "cloakroom[model,ocr]"
    assert update.spec(update.extras(lambda m: None)) == "cloakroom"


def fake_run(calls, codex_program=None):
    def run(argv, **kw):
        calls.append(list(argv))
        if argv[1:3] == ["install", "codex"] and codex_program:
            agents.install_codex(codex_program)  # what the new program writes
        return subprocess.CompletedProcess(argv, 0, stdout="cloakroom 0.8.0\n" if "--version" in argv else "")
    return run


def test_update_refreshes_only_what_was_installed_and_asks_for_codex_trust(home, monkeypatch, capsys):
    agents.install_codex("/old/cloakroom")
    monkeypatch.setattr(agents, "_program", lambda: "/new/cloakroom")
    monkeypatch.setattr(update, "route", lambda: ("uv", ["uv", "tool", "install", "--force", "cloakroom[ocr]"], None))
    from cloakroom import client
    monkeypatch.setattr(client, "health", lambda port: {"service": "cloakroom", "version": "0.7.1"})
    calls = []
    monkeypatch.setattr(update.subprocess, "run", fake_run(calls, "/new/cloakroom"))
    assert cli.main(["update"]) == 0
    assert calls == [["uv", "tool", "install", "--force", "cloakroom[ocr]"], ["/new/cloakroom", "--version"],
                     ["/new/cloakroom", "install", "codex"]]  # no Gemini, no git hook: neither was installed
    out = capsys.readouterr().out
    assert "trust each Cloakroom entry" in out and "Installed now: cloakroom 0.8.0" in out
    assert "still running version 0.7.1" in out and "Claude Code: nothing changed there" in out
    calls.clear()  # the same hook text again: no trust reminder
    assert cli.main(["update"]) == 0 and "trust each Cloakroom entry" not in capsys.readouterr().out


def test_update_knows_cursor(home, monkeypatch, capsys):
    assert "cursor" not in update.installed()
    agents.install_cursor("/old/cloakroom")
    assert update.installed() == ["cursor"]
    monkeypatch.setattr(update, "route", lambda: ("pipx", ["pipx", "install", "--force", "cloakroom"], None))
    monkeypatch.setattr(update.subprocess, "run", lambda *a, **k: pytest.fail("--check ran a command"))
    assert cli.main(["update", "--check"]) == 0
    assert "for Cursor's command-line agent again: cloakroom install cursor" in capsys.readouterr().out


def test_cursor_home_follows_the_home_env(tmp_path, monkeypatch):
    # Windows' expanduser ignores HOME; without this, the tests above would write into the real ~/.cursor there
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(agents.os.path, "expanduser", lambda p: "/elsewhere")
    assert agents.cursor_home() == str(tmp_path / ".cursor")


def test_a_failed_upgrade_refreshes_nothing(home, monkeypatch, capsys):
    agents.install_gemini("/old/cloakroom")
    monkeypatch.setattr(update, "route", lambda: ("pip", ["python", "-m", "pip", "install", "-U", "cloakroom"], None))
    calls = []
    monkeypatch.setattr(update.subprocess, "run",
                        lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 1))
    assert cli.main(["update"]) == 1 and len(calls) == 1
    assert f"Version {update.__version__} stays" in capsys.readouterr().err


def test_check_changes_nothing(home, monkeypatch, capsys):
    agents.install_codex("/old/cloakroom")
    agents.install_gemini("/old/cloakroom")
    before = (home / ".codex" / "hooks.json").read_text()
    monkeypatch.setattr(update, "route", lambda: ("pipx", ["pipx", "install", "--force", "cloakroom"], None))
    monkeypatch.setattr(update.subprocess, "run", lambda *a, **k: pytest.fail("--check ran a command"))
    assert cli.main(["update", "--check"]) == 0
    out = capsys.readouterr().out
    assert "update Cloakroom with pipx, the installer you used: pipx install --force cloakroom" in out
    assert "for Codex again: cloakroom install codex" in out and "for Gemini CLI again: cloakroom install gemini" in out
    assert (home / ".codex" / "hooks.json").read_text() == before


def test_unknown_install_prints_the_steps(home, monkeypatch, capsys):
    agents.install_codex("/old/cloakroom")
    monkeypatch.setattr(update, "route", lambda: (None, ["/py", "-m", "pip", "install", "-U", "cloakroom[ocr]"],
                                                  "can't tell how this copy was installed"))
    monkeypatch.setattr(update.subprocess, "run", lambda *a, **k: pytest.fail("ran a command"))
    assert cli.main(["update"]) == 1
    out = capsys.readouterr().out
    assert '1. /py -m pip install -U "cloakroom[ocr]"' in out and "2. cloakroom install codex" in out


def test_git_hook_of_this_repo_counts_as_installed(home, monkeypatch):
    from cloakroom import githook
    monkeypatch.setattr(update, "_git_hook_here", GIT_HOOK_HERE)
    assert "git-hook" not in update.installed()  # not a repo
    subprocess.run(["git", "init", "-q"], check=True)
    assert "git-hook" not in update.installed()
    monkeypatch.setattr(agents, "_program", lambda: "/usr/local/bin/cloakroom")
    githook.install()
    assert update.installed() == ["git-hook"]


def plugins(home, *versions):
    d = home / ".claude" / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    (d / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        "other@x": [{"version": "9.9.9"}],
        "cloakroom@cloakroom": [{"scope": "user", "version": v} for v in versions]}}))


def test_doctor_prints_the_version_and_a_plugin_mismatch(home, capsys):
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert f"version: cloakroom {__version__}" in out and "claude plugin" not in out  # no plugin: no line
    plugins(home, __version__)
    cli.main(["doctor"])
    assert f"ok   claude plugin: cloakroom@cloakroom {__version__}, the same version" in capsys.readouterr().out
    plugins(home, "0.5.0", "0.5.0")
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert out.count("claude plugin:") == 1 and "warn  claude plugin: cloakroom@cloakroom is version 0.5.0, older than" in out
    assert "claude plugin update cloakroom@cloakroom" in out and "9.9.9" not in out
    assert "Restart Claude Code" in update.plugin_line("cloakroom@cloakroom", "99.0.0")
    assert update.older("0.7.1", "0.10.0") and not update.older("0.8.0", "0.8.0rc1")
