"""Upgrades when the plugin updates: the version compare in plugin/scripts/upgrade_probe.py, the stamp check in the
hooks (scripts/gen_hooks.py), and bootstrap.sh --upgrade, run with stub programs in a throwaway HOME.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugin" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "scripts"))
import gen_hooks  # noqa: E402
import upgrade_probe as probe  # noqa: E402

PIN = json.loads((ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text())["version"]
TOOLS = ("bash", "sh", "mkdir", "chmod", "tail", "find", "rm", "date", "mktemp", "cat", "grep", "uname", "dirname",
         "shasum", "sha256sum", "openssl", "perl", "ln", "printf", "env", "sed", "readlink", "ps", "sleep", "touch",
         "nohup", "echo")
needs_bash = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash")


# --- the version compare and the probe ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,want", [
    ("0.4.2", "0.5.0", -1), ("0.5.0", "0.5.0", 0), ("0.5.1", "0.5.0", 1), ("0.10.0", "0.9.9", 1),
    ("0.5", "0.5.0", 0), ("1.0.0", "0.99.99", 1), ("0.5.0rc1", "0.5.0", -1), ("0.5.0rc2", "0.5.0rc1", 1),
    ("0.5.0b1", "0.5.0rc1", 0), ("0.5.0.post1", "0.5.0", 1), ("0.5.0a1", "0.4.9", 1), ("v0.5.0", "0.5.0", 0),
])
def test_compare(a, b, want):
    assert probe.compare(a, b) == want


def test_compare_refuses_what_isnt_a_release():
    with pytest.raises(ValueError):
        probe.compare("main", "0.5.0")


@pytest.mark.parametrize("version,direct_url,want", [
    ("0.4.2", None, "older"),
    ("0.5.0", None, "same"),
    ("0.6.0", None, "newer"),                                         # installed by hand: never downgraded
    ("0.4.2", json.dumps({"url": "file:///src", "dir_info": {"editable": True}}), "dev"),
    ("0.4.2", json.dumps({"url": "file:///src", "dir_info": {}}), "dev"),  # built from a source folder
    ("0.4.2", json.dumps({"url": "file:///w/cloakroom-0.4.2-py3-none-any.whl", "archive_info": {}}), "older"),
    ("0.5.0.dev3", None, "dev"), ("0.4.2+local", None, "dev"), ("weird", None, "dev"), ("0.4.2", "{broken", "dev"),
])
def test_kind(version, direct_url, want):
    assert probe.kind(version, direct_url, "0.5.0") == want


def test_extras_seen_from_packages():
    have = {"onnxruntime", "tokenizers", "rapidocr"}
    assert probe.extras(lambda m: object() if m in have else None) == "model,ocr"
    assert probe.extras(lambda m: None) == "-"
    assert probe.extras(lambda m: object()) == "model,torch,ocr"


def test_probe_runs_isolated_with_the_standard_library():
    r = subprocess.run([sys.executable, "-I", str(SCRIPTS / "upgrade_probe.py"), PIN], capture_output=True, text=True)
    kind, version, extras, prefix = r.stdout.strip().split(" ", 3)
    assert r.returncode == 0 and kind in {"older", "same", "newer", "dev", "missing"}
    assert prefix == sys.prefix


def test_pin_is_the_same_everywhere():
    """plugin.json, both installers and both generated hooks name the same release."""
    sh, ps1 = (SCRIPTS / "bootstrap.sh").read_text(), (SCRIPTS / "bootstrap.ps1").read_text()
    assert re.search(r"^PIN=(\S+)", sh, re.M).group(1) == PIN
    assert re.search(r'^\$Pin = "([^"]+)"', ps1, re.M).group(1) == PIN
    assert f"\nPIN={PIN}\n" in (SCRIPTS / "hook.sh").read_text()
    assert f"\n$pin = '{PIN}'\n" in (SCRIPTS / "hook.ps1").read_text()
    for text in (sh, ps1):  # the upgrade installs this release, spelled out
        assert f"]=={PIN}" in text and f'"cloakroom=={PIN}"' in text


# --- sandbox -------------------------------------------------------------------------------------------------------

def stub(path: pathlib.Path, body: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return path


def sandbox(tmp_path):
    home, bindir = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    bindir.mkdir()
    for t in TOOLS:
        real = shutil.which(t, path="/usr/bin:/bin:/usr/sbin:/sbin")
        if real:
            (bindir / t).symlink_to(real)
    env = {"HOME": str(home), "PATH": str(bindir), "TMPDIR": str(tmp_path), "SHELL": "/bin/bash",
           "CLOAKROOM_PLATFORM": "Linux-x86_64"}
    return env, bindir, home / ".cloakroom"


def wait_for(path: pathlib.Path, seconds=10.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if path.exists():
            return True
        time.sleep(0.1)
    return False


# --- the hook's stamp check ----------------------------------------------------------------------------------------

@pytest.fixture
def hooked(tmp_path):
    """A plugin copy whose bootstrap.sh only records how it was called, and an installed cloakroom stub."""
    env, _, h = sandbox(tmp_path)
    plugin = tmp_path / "plugin"
    shutil.copytree(ROOT / "plugin", plugin)
    called = tmp_path / "bootstrap-called"
    stub(plugin / "scripts" / "bootstrap.sh", f'echo "$*" >> "{called}"')
    stub(pathlib.Path(env["HOME"]) / ".local" / "bin" / "cloakroom", 'echo "{\\"from\\": \\"cloakroom $*\\"}"')
    h.mkdir()
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin)

    def hook(event="pre"):
        r = subprocess.run([shutil.which("bash"), str(plugin / "scripts" / "hook.sh"), event], env=env, input="{}",
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        return r.stdout
    return hook, h, called


@needs_bash
def test_hook_with_current_stamp_starts_nothing(hooked):
    hook, h, called = hooked
    (h / "upgrade.stamp").write_text(PIN + "\n")
    assert '"cloakroom hook pre"' in hook()
    time.sleep(0.5)
    assert not called.exists()


@needs_bash
@pytest.mark.parametrize("stamp", [None, "0.4.2", "failed:0.4.2"])
def test_hook_starts_the_upgrade_and_keeps_the_old_program(hooked, stamp):
    hook, h, called = hooked
    if stamp:
        (h / "upgrade.stamp").write_text(stamp + "\n")
    assert '"cloakroom hook pre"' in hook()  # this call is still answered by the installed program
    assert wait_for(called) and called.read_text().strip() == "--upgrade"


@needs_bash
def test_failed_upgrade_retries_after_ten_minutes(hooked):
    hook, h, called = hooked
    st = h / "upgrade.stamp"
    st.write_text(f"failed:{PIN}\n")
    hook()
    time.sleep(0.5)
    assert not called.exists()
    old = time.time() - 11 * 60
    os.utime(st, (old, old))
    hook()
    assert wait_for(called)


@needs_bash
def test_no_second_upgrade_while_the_installer_runs(hooked):
    hook, h, called = hooked
    (h / "bootstrap.lock").mkdir()
    hook()
    time.sleep(0.5)
    assert not called.exists()


@needs_bash
@pytest.mark.parametrize("answer", ["", "not json", '{"cut short'])
def test_hook_fails_closed_when_the_program_gives_no_answer(hooked, tmp_path, answer):
    """Same rule as hook.ps1: a program at the PIN always answers (at least {}), so silence or garbage is broken."""
    hook, h, _ = hooked
    (h / "upgrade.stamp").write_text(PIN + "\n")
    prog = tmp_path / "home" / ".local" / "bin" / "cloakroom"
    stub(prog, f"cat >/dev/null; printf '%s' '{answer}'")
    assert "gave no answer" in json.loads(hook("post"))["hookSpecificOutput"]["updatedToolOutput"]
    assert json.loads(hook("pre"))["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "gave no answer" in json.loads(hook("prompt"))["systemMessage"]  # prompts still pass, with a warning
    stub(prog, "cat >/dev/null; printf '{}\\r\\n'")  # Python on Windows ends the line with CRLF
    assert hook("post") == "{}\n"  # a clean pass stays a pass


@needs_bash
def test_hook_lets_an_older_program_answer_nothing(hooked, tmp_path):
    """A program older than the PIN (waiting for its upgrade) prints nothing for a clean call: still a pass."""
    hook, h, called = hooked
    (h / "upgrade.stamp").write_text("0.4.2\n")
    stub(tmp_path / "home" / ".local" / "bin" / "cloakroom", "cat >/dev/null")
    assert hook("post") == ""
    assert wait_for(called)


@needs_bash
def test_notice_shown_once_at_session_start(hooked):
    hook, h, _ = hooked
    (h / "upgrade.stamp").write_text(PIN + "\n")
    note = {"systemMessage": "cloakroom: version 0.4.2 is installed ... To finish: 1. Run: x 2. Then restart"}
    (h / "upgrade.notice").write_text(json.dumps(note) + "\n")
    assert '"cloakroom hook pre"' in hook("pre")  # never mixed into a tool call's answer
    out = hook("start")
    assert json.loads(out) == note  # one JSON answer: the notice, not the program's
    assert not (h / "upgrade.notice").exists()
    assert '"cloakroom hook start"' in hook("start")


# --- bootstrap.sh --upgrade ----------------------------------------------------------------------------------------

@pytest.fixture
def installed(tmp_path):
    """An installed cloakroom stub: a script whose #! names its environment's python (a stub that answers the
    probe), and uv / pipx stubs that record what they install."""
    env, bindir, h = sandbox(tmp_path)
    home = pathlib.Path(env["HOME"])
    log = tmp_path / "installs"
    tools = home / ".local" / "share" / "uv" / "tools"
    pipxv = home / ".local" / "share" / "pipx" / "venvs"
    stub(bindir / "uv", f'case "$*" in "tool dir --bin") echo "{home}/.local/bin" ;; "tool dir") echo "{tools}" ;; '
                        f'"tool install"*) echo "uv $* hold=$([ -e "{h}/bootstrap.lock/upgrade" ] && echo y || echo n)" >> "{log}" ;; '
                        'esac')
    stub(bindir / "pipx", f'case "$1" in environment) echo "{pipxv}" ;; install) echo "pipx $*" >> "{log}" ;; esac')
    h.mkdir()

    def make(route="uv", answer="older 0.4.2 model,torch", runs=True):
        prefix = {"uv": tools / "cloakroom", "pipx": pipxv / "cloakroom", "venv": h / "venv",
                  "other": tmp_path / "brew" / "cloakroom"}[route]
        calls = tmp_path / "program-calls"
        # the env python: answers the probe, records pip installs, else runs the script it is given
        stub(prefix / "bin" / "python", f'case "$*" in *upgrade_probe.py*) echo "{answer} {prefix}" ;; '
                                       f'*pip*) echo "pip $*" >> "{log}" ;; *) exec /bin/sh "$@" ;; esac')
        # the program: `serve` waits (and dies on TERM), --version answers the new release
        prog = stub(prefix / "bin" / "cloakroom", f'echo "$* $$" >> "{calls}"; '
                                                  f'[ "$1" = --version ] && {{ {"" if runs else "exit 1; "}echo "cloakroom {PIN}"; }}; '
                                                  '[ "$1" = serve ] && { sleep 60 & wait; }; exit 0')
        prog.write_text(f"#!{prefix}/bin/python\n" + prog.read_text().split("\n", 1)[1])
        (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
        (home / ".local" / "bin" / "cloakroom").symlink_to(prog)
        return prefix, calls

    def run(extra=None):
        e = {**env, **(extra or {})}
        return subprocess.run([shutil.which("bash"), str(SCRIPTS / "bootstrap.sh"), "--upgrade"], env=e,
                              capture_output=True, text=True, timeout=60)
    return make, run, h, log, env


def boot_log(h):
    return (h / "bootstrap.log").read_text()


@needs_bash
@pytest.mark.parametrize("route,want", [
    ("uv", f'uv tool install --python 3.12 --force cloakroom[model,torch]=={PIN}'),
    ("pipx", f"pipx install --force cloakroom[model,torch]=={PIN}"),
    ("venv", f"pip -m pip install -q -U cloakroom[model,torch]=={PIN}"),
])
def test_upgrade_on_the_route_it_came_from_with_its_extras(installed, route, want):
    make, run, h, log, _ = installed
    make(route)
    assert run().returncode == 0
    assert want in log.read_text()
    text = boot_log(h)
    assert f"upgrading 0.4.2 -> {PIN}" in text and f"done: cloakroom {PIN}" in text
    if route == "uv":
        assert "hold=y" in log.read_text()  # hooks were held while the files were swapped
    assert not (h / "bootstrap.lock").exists()  # and released after
    assert (h / "upgrade.stamp").read_text().strip() == PIN
    assert not (h / "upgrade.notice").exists()


@needs_bash
@pytest.mark.parametrize("platform,mac", [("Linux-x86_64", False), ("Darwin-arm64", True)])
def test_keychain_line_only_on_a_mac_and_never_sets_the_exit_status(installed, platform, mac):
    """The Darwin-only Keychain line is the last one of a successful upgrade: on Linux it must not end it with 1."""
    make, run, h, _, _ = installed
    make("uv")
    assert run({"CLOAKROOM_PLATFORM": platform}).returncode == 0, boot_log(h)
    assert ("click Always Allow" in boot_log(h).replace("Click", "click")) == mac


@needs_bash
@pytest.mark.parametrize("answer", ["newer 0.9.0 -", "dev 0.4.2 model", "same 0.5.0 -"])
def test_newer_dev_and_current_installs_are_left_alone(installed, answer):
    make, run, h, log, _ = installed
    make("uv", answer)
    assert run().returncode == 0
    assert not log.exists()  # nothing installed
    assert (h / "upgrade.stamp").read_text().strip() == PIN
    assert not (h / "upgrade.notice").exists()  # silent
    assert "left as is" in boot_log(h)


@needs_bash
def test_older_install_elsewhere_gets_the_steps_once(installed):
    make, run, h, log, _ = installed
    prefix, _ = make("other", "older 0.4.2 -")
    assert run().returncode == 0
    assert not log.exists()
    msg = json.loads((h / "upgrade.notice").read_text())["systemMessage"]
    assert "1. Run: " in msg and "2. Then restart Claude Code" in msg
    assert f'{prefix}/bin/python -m pip install -U "cloakroom=={PIN}"' in msg
    assert (h / "upgrade.stamp").read_text().strip() == PIN


@needs_bash
def test_failed_upgrade_keeps_the_old_program_and_retries(installed, tmp_path):
    make, run, h, log, _ = installed
    make("uv")
    stub(tmp_path / "bin" / "uv", f'case "$1 $2" in "tool dir") echo "{pathlib.Path(tmp_path, "home", ".local", "share", "uv", "tools")}" ;; '
                                  '*) exit 1 ;; esac')
    assert run().returncode == 1
    assert (h / "upgrade.stamp").read_text().strip() == f"failed:{PIN}"
    assert "upgrade failed: cloakroom 0.4.2 stays in use; it tries again in 10 minutes" in boot_log(h)


@needs_bash
def test_running_service_restarts_on_the_new_version(installed):
    make, run, h, _, _ = installed
    prefix, calls = make("uv")
    old = subprocess.Popen([str(prefix / "bin" / "python"), str(prefix / "bin" / "cloakroom"), "serve"])
    try:
        time.sleep(0.3)
        assert run().returncode == 0
        old.wait(timeout=15)  # the old service was stopped
        end = time.time() + 10
        while time.time() < end and sum(l.startswith("serve ") for l in calls.read_text().splitlines()) < 2:
            time.sleep(0.1)
        assert sum(l.startswith("serve ") for l in calls.read_text().splitlines()) == 2  # and a new one started
        assert "service restarted on the new version" in boot_log(h)
    finally:
        if old.poll() is None:
            old.kill()
        for line in calls.read_text().splitlines():
            if line.startswith("serve "):
                try:
                    os.kill(int(line.split()[-1]), 9)
                except (OSError, ValueError):
                    pass


@needs_bash
def test_fresh_install_writes_the_stamp(tmp_path):
    """A new install never runs an upgrade check on its first call: stage 1 leaves the stamp."""
    env, bindir, h = sandbox(tmp_path)
    prog = pathlib.Path(env["HOME"]) / ".local" / "bin" / "cloakroom"
    stub(bindir / "uv", f'case "$1 $2" in "tool install") mkdir -p "{prog.parent}"; printf "#!/bin/sh\\nexit 0\\n" > "{prog}"; '
                        f'chmod 755 "{prog}" ;; "tool dir") echo "{tmp_path}/tools" ;; esac; exit 0')
    env["CLOAKROOM_PLATFORM"] = "Darwin-x86_64"  # an Intel Mac stops after stage 1
    r = subprocess.run([shutil.which("bash"), str(SCRIPTS / "bootstrap.sh")], env=env, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, boot_log(h)
    assert (h / "upgrade.stamp").read_text().strip() == PIN


def test_generator_reads_the_plugin_version():
    assert gen_hooks.PIN == PIN


@needs_bash
def test_hook_holds_tool_calls_while_files_are_swapped(hooked):
    hook, h, called = hooked
    (h / "bootstrap.lock").mkdir()
    (h / "bootstrap.lock" / "upgrade").write_text("")
    out = hook("pre")
    assert "updating itself" in out and '"permissionDecision": "deny"' in out
    assert "cloakroom hook" not in out  # the old program is never started
    assert "updating itself" in hook("post") and "updatedToolOutput" in hook("post")
    old = time.time() - 31 * 60  # a hold left by a killed installer goes stale
    os.utime(h / "bootstrap.lock" / "upgrade", (old, old))
    assert '"cloakroom hook pre"' in hook("pre")


def reinstall_uv(bindir, home, log, works=True):
    """A uv whose uninstall removes the program and whose install puts a working (or no) program in place."""
    prog = home / ".local" / "bin" / "cloakroom"
    make = (f'mkdir -p "{prog.parent}"; rm -f "{prog}"; printf "#!/bin/sh\\necho cloakroom {PIN}\\n" > "{prog}"; '
            f'chmod 755 "{prog}"' if works else "exit 1")
    stub(bindir / "uv", f'echo "uv $*" >> "{log}"; case "$*" in "tool dir --bin") echo "{prog.parent}" ;; '
                        f'"tool dir") echo "{home}/.local/share/uv/tools" ;; '
                        f'"tool uninstall"*) rm -rf "{prog}" "{home}/.local/share/uv/tools/cloakroom" ;; '
                        f'"tool install"*) {make} ;; esac; exit 0')


@needs_bash
@pytest.mark.parametrize("answer,runs", [("missing - -", True),             # the env lost the cloakroom module
                                         ("older 0.4.2 -", False)])         # --version fails
def test_broken_program_is_reinstalled_from_scratch(installed, tmp_path, answer, runs):
    make, run, h, log, env = installed
    make("uv", answer, runs=runs)
    reinstall_uv(tmp_path / "bin", pathlib.Path(env["HOME"]), log)
    assert run().returncode == 0, boot_log(h)
    text, installs = boot_log(h), log.read_text()
    assert "does not run" in text and "removing it (uv) and installing again" in text
    assert "uv tool uninstall cloakroom" in installs
    assert f"cloakroom=={PIN}" in installs and f"cloakroom[model,torch,ocr]=={PIN}" in installs  # stage 1, stage 2
    assert "stage 1 of 2" in text and "done: full protection" in text
    assert (h / "upgrade.stamp").read_text().strip() == PIN  # only once --version works


@needs_bash
def test_failed_reinstall_ends_failed_with_steps_and_no_stamp(installed, tmp_path):
    make, run, h, log, env = installed
    make("uv", "missing - -")
    reinstall_uv(tmp_path / "bin", pathlib.Path(env["HOME"]), log, works=False)
    run()
    assert not (h / "upgrade.stamp").exists()
    assert (h / "bootstrap.state").read_text().strip() == "failed"
    text = boot_log(h)
    assert "stage 1 failed" in text and "1. Read the error" in text
    # the hook now finds no program: tool calls denied with the steps, retried after 10 minutes
    e = {**env, "CLAUDE_PLUGIN_ROOT": str(ROOT / "plugin")}
    r = subprocess.run([shutil.which("bash"), str(SCRIPTS / "hook.sh"), "pre"], env=e, input="{}",
                       capture_output=True, text=True, timeout=30)
    assert "the automatic install failed; it tries again in 10 minutes" in r.stdout and '"deny"' in r.stdout


def test_windows_upgrade_is_a_clean_reinstall():
    """bootstrap.ps1 -Upgrade never replaces an environment in place (on Windows that left environments without the
    cloakroom package): it uninstalls, then installs without --force, inside the hold."""
    ps1 = (SCRIPTS / "bootstrap.ps1").read_text()
    body = ps1[ps1.index("function Install-Clean"):ps1.index("if ($Upgrade) {")]
    assert not re.search(r"& \S+ (tool )?install[^\n]*--force", body)  # no command replaces in place
    assert "upgrading $ver -> $Pin (clean reinstall on Windows" in body
    swap = body[body.index("Set-Hold\n  $was = Stop-CloakroomService"):]
    order = [swap.index(k) for k in ("Wait-Idle $marks 60", "Remove-Install $route $prefix $uv\n  $ok = Install-Clean",
                                     "Clear-Hold\n  if ($was -gt 0)", "Write-Plain $Stamp $Pin")]
    assert order == sorted(order)
    tail = swap[swap.index("Install-Clean $route $spec $uv"):]
    assert tail.index("--version") < tail.index("Write-Plain $Stamp $Pin")  # stamped only once the program runs


def test_windows_first_install_stamps_after_the_last_stage():
    ps1 = (SCRIPTS / "bootstrap.ps1").read_text()
    main = ps1[ps1.index("stage 1 of 2: installing"):]
    first_stamp = main.index("Write-Plain $Stamp $Pin")
    assert main.index("stage 2 of 2: installing the model") < first_stamp
    assert main.index("$cr = Find-Cloakroom") < first_stamp and main.index("--version") < first_stamp


def test_unix_first_install_stamps_after_stage_one_runs():
    sh = (SCRIPTS / "bootstrap.sh").read_text()
    main = sh[sh.index('echo "$(date \'+%F %T\') stage 1 of 2'):]
    assert main.index('"$CR" --version') < main.index('echo "$PIN" >"$STAMP"')


@pytest.mark.parametrize("script", ["bootstrap.ps1", "hook.ps1"])
def test_no_native_call_piped_straight_into_select_object(script):
    """Windows PowerShell 5.1: `& prog | Select-Object -First 1` stops prog early and sets $LASTEXITCODE to -1, so a
    working program reads as broken. The native call must be parenthesised first: `(& prog) | Select-Object`."""
    text = (SCRIPTS / script).read_text() + (ROOT / "scripts" / "gen_hooks.py").read_text()
    bad = re.findall(r"&[^()|\n]*\|\s*Select-Object[^\n]*", text)
    assert not bad, bad
