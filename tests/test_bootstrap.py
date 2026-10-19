"""The macOS/Linux first-start installer (plugin/scripts/bootstrap.sh) and the hook messages it pairs with.

Each test runs the real script with a PATH that holds only the system tools it needs plus stubs (curl, wget, uv, pipx,
python3), in a throwaway HOME, so nothing is downloaded or installed.
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugin" / "scripts"
BOOT = SCRIPTS / "bootstrap.sh"
TOOLS = ("bash", "sh", "mkdir", "chmod", "tail", "find", "rm", "date", "mktemp", "cat", "grep", "uname", "dirname",
         "shasum", "sha256sum", "openssl", "perl", "ln", "printf", "env")

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash")
sys.path.insert(0, str(ROOT / "scripts"))
import gen_hooks  # noqa: E402


def stub(bindir: pathlib.Path, name: str, body: str) -> None:
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)


def sandbox(tmp_path: pathlib.Path) -> tuple[dict, pathlib.Path]:
    """A HOME and a PATH with only the system tools bootstrap.sh uses (no curl, wget, uv, pipx or python)."""
    home, bindir = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    bindir.mkdir()
    for t in TOOLS:
        real = shutil.which(t, path="/usr/bin:/bin:/usr/sbin:/sbin")
        if real:
            (bindir / t).symlink_to(real)
    env = {"HOME": str(home), "PATH": str(bindir), "TMPDIR": str(tmp_path), "SHELL": "/bin/bash",
           "CLOAKROOM_PLATFORM": "Linux-x86_64"}
    return env, bindir


def run(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([shutil.which("bash"), str(BOOT), *args], env=env, capture_output=True, text=True, timeout=60)


def home(env: dict) -> pathlib.Path:
    return pathlib.Path(env["HOME"]) / ".cloakroom"


def test_scripts_parse():
    for script in ("bootstrap.sh", "hook.sh"):
        r = subprocess.run(["bash", "-n", str(SCRIPTS / script)], capture_output=True, text=True)
        assert r.returncode == 0, (script, r.stderr)


@pytest.mark.parametrize("tool,body,ok", [
    (None, "", False),
    ("curl", "exit 0", True),
    ("wget", "exit 0", True),
    ("uv", "exit 0", True),
    ("pipx", "exit 0", True),
    ("python3", "exit 0", True),   # the stub answers the 3.10+ test with yes
    ("python3", "exit 1", False),  # an older Python can't lead to an install
])
def test_check_says_whether_anything_can_install(tmp_path, tool, body, ok):
    env, bindir = sandbox(tmp_path)
    if tool:
        stub(bindir, tool, body)
    r = run(env, "--check")
    assert (r.returncode == 0) is ok
    log = home(env) / "bootstrap.log"
    if ok:
        assert not log.exists()
    else:  # the steps go to the log once, however often the hook asks
        run(env, "--check")
        lines = log.read_text().splitlines()
        assert len(lines) == 1 and "1. Install curl: sudo apt install curl" in lines[0]
        assert "sudo dnf install curl" in lines[0] and "restart Claude Code" in lines[0] and "Log: " in lines[0]


def test_check_finds_uv_where_its_installer_puts_it(tmp_path):
    env, _ = sandbox(tmp_path)
    uv = pathlib.Path(env["HOME"]) / ".local" / "bin"
    uv.mkdir(parents=True)
    stub(uv, "uv", "exit 0")
    assert run(env, "--check").returncode == 0


def test_wrong_checksum_never_runs_the_uv_installer(tmp_path):
    env, bindir = sandbox(tmp_path)
    marker = tmp_path / "ran"
    # a "curl" that serves a different installer: it must not run
    stub(bindir, "curl", 'while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done\n'
                         f'printf "touch {marker}\\n" > "$out"')
    r = run(env)
    log = (home(env) / "bootstrap.log").read_text()
    assert r.returncode == 1 and not marker.exists()
    assert "uv installer checksum mismatch" in log and "not running it" in log
    assert (home(env) / "bootstrap.state").read_text().strip() == "failed"
    assert (home(env) / "bootstrap.reason").read_text().strip() == "checksum"
    assert "1. Install uv yourself" in log and "restart Claude Code" in log


def test_offline_is_told_apart(tmp_path):
    env, bindir = sandbox(tmp_path)
    stub(bindir, "curl", "exit 6")  # curl: could not resolve host
    assert run(env).returncode == 1
    assert (home(env) / "bootstrap.reason").read_text().strip() == "offline"
    log = (home(env) / "bootstrap.log").read_text()
    assert "could not download the uv installer (curl exit 6)" in log and "1. Connect to the internet" in log


def test_python_without_venv_and_no_downloader(tmp_path):
    env, bindir = sandbox(tmp_path)
    # passes the 3.10+ test, fails "import venv, ensurepip" (Debian/Ubuntu without python3-venv)
    stub(bindir, "python3", 'case "$2" in *venv*) exit 1 ;; esac; exit 0')
    assert run(env, "--check").returncode == 0
    assert run(env).returncode == 1
    log = (home(env) / "bootstrap.log").read_text()
    assert "installer: novenv" in log and "sudo apt install python3-venv" in log
    assert (home(env) / "bootstrap.reason").read_text().strip() == "nofetch"
    assert "1. Install curl: sudo apt install curl" in log


def test_mac_without_anything_suggests_homebrew_uv(tmp_path):
    env, _ = sandbox(tmp_path)
    env["CLOAKROOM_PLATFORM"] = "Darwin-arm64"
    assert run(env, "--check").returncode == 1
    assert "1. Install uv: brew install uv" in (home(env) / "bootstrap.log").read_text()


def test_uv_pin_is_the_same_everywhere():
    sh, ps1 = BOOT.read_text(), (SCRIPTS / "bootstrap.ps1").read_text()
    version = re.search(r"^UV_VERSION=(\S+)", sh, re.M).group(1)
    sh_sum = re.search(r"^UV_SH_SHA256=([0-9a-f]{64})$", sh, re.M).group(1)
    ps_sum = re.search(r'^\$UvPs1Sha256 = "([0-9a-f]{64})"', ps1, re.M).group(1)
    assert f'$UvVersion = "{version}"' in ps1
    assert 'UV_SH_URL="https://astral.sh/uv/$UV_VERSION/install.sh"' in sh
    assert '$UvPs1Url = "https://astral.sh/uv/$UvVersion/install.ps1"' in ps1
    readme = (ROOT / "plugin" / "README.md").read_text()
    assert f"uv {version}" in readme and sh_sum in readme and ps_sum in readme


def test_hook_messages_give_numbered_steps():
    for msg in gen_hooks.MESSAGES[1:]:
        assert "1. " in msg and "2. " in msg, msg
        assert "restart Claude Code" in msg and "~/.cloakroom/bootstrap.log" in msg, msg
        assert "'" not in msg, msg  # the hook scripts hold them in single quotes
    sh = BOOT.read_text()
    for cmd in ("sudo apt install curl", "sudo dnf install curl", "brew install uv", "releases.astral.sh",
                "sudo apt install python3-venv", "Then restart Claude Code (/exit, then claude)"):
        assert any(cmd in m for m in gen_hooks.MESSAGES), cmd
        assert cmd in sh, cmd  # the log carries the same steps


@pytest.mark.parametrize("reason,expect", [("offline", "could not reach the internet"),
                                           ("checksum", "did not match its pinned checksum"),
                                           ("nofetch", "nothing to install it with"),
                                           ("", "Read the error at the end")])
def test_hook_shows_the_steps_for_the_reason(tmp_path, reason, expect):
    env, _ = sandbox(tmp_path)
    h = home(env)
    h.mkdir()
    (h / "bootstrap.state").write_text("failed\n")
    if reason:
        (h / "bootstrap.reason").write_text(reason + "\n")
    env["CLAUDE_PLUGIN_ROOT"] = str(ROOT / "plugin")
    r = subprocess.run([shutil.which("bash"), str(SCRIPTS / "hook.sh"), "pre"], env=env, input="{}",
                       capture_output=True, text=True, timeout=30)
    out = r.stdout
    assert r.returncode == 0 and expect in out and '"permissionDecision": "deny"' in out, out
