"""cloakroom update [--check]: upgrade the program the way it was installed, then refresh what it installed elsewhere.

The route comes from the program's own environment: a uv tool (uv-receipt.toml), a pipx venv (pipx_metadata.json)
or pip (the INSTALLER record). Anything else, a source checkout, or Windows (a running program can't replace its own
files there) gets the command printed instead. The extras whose packages are present are kept. Then Codex's hooks,
Gemini CLI's extension, Cursor's hooks and this repo's git hook are written again by the new program, only where already installed.
Claude Code's plugin is never touched: Claude Code updates it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from importlib import metadata, util

from . import __version__, agents

EXTRAS = (("model", ("onnxruntime", "tokenizers")), ("torch", ("torch",)), ("ocr", ("rapidocr",)))  # as upgrade_probe
NAMES = {"codex": "Codex", "gemini": "Gemini CLI", "cursor": "Cursor's command-line agent", "git-hook": "git commits in this project"}
CLAUDE_NOTE = ("Claude Code: nothing changed there. Claude Code updates its Cloakroom plugin itself (to switch that "
               "on: type /plugin in Claude Code, go to Marketplaces, choose cloakroom, then Enable auto-update), and "
               "the plugin then updates this program.")


def plugin_versions() -> list[tuple[str, str]]:
    """(name@marketplace, version) of each Cloakroom plugin Claude Code has installed; read from disk, no network."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    try:
        with open(os.path.join(base, "plugins", "installed_plugins.json"), encoding="utf-8") as f:
            plugins = json.load(f).get("plugins", {})
        return sorted({(k, str(e["version"])) for k, es in plugins.items() if k.split("@")[0] == "cloakroom"
                       for e in es if isinstance(e, dict) and e.get("version")})
    except (OSError, ValueError, AttributeError, TypeError):
        return []


def older(a: str, b: str) -> bool:
    """a's release numbers are below b's (0.7.1 < 0.8.0); pre-release tags are ignored."""
    def nums(v):
        m = re.match(r"\d+(?:\.\d+)*", v)
        return tuple(int(x) for x in m.group().split(".")) if m else ()
    return nums(a) < nums(b)


def plugin_line(key: str, version: str) -> str:
    """doctor's line for one Claude Code plugin install: its version and, when it differs, what to do."""
    if version == __version__:
        return f"{key} {version}, the same version as this program"
    if older(__version__, version):
        return (f"{key} is version {version}, but this program is still {__version__}. Restart Claude Code (type "
                "/exit, then claude) and the plugin updates the program, or run: cloakroom update")
    return (f"{key} is version {version}, older than this program ({__version__}). That's fine: Claude Code updates "
            f"it a few days after each release. To update it now, run this in a terminal: claude plugin update {key}, "
            "then type /reload-plugins in Claude Code")


def extras(find=util.find_spec) -> list[str]:
    return [name for name, mods in EXTRAS if all(find(m) is not None for m in mods)]


def spec(have: list[str]) -> str:
    return f"cloakroom[{','.join(have)}]" if have else "cloakroom"


def _dev(dist) -> bool:
    """Built from a source folder (editable or not), or a .dev / +local version: the user updates that themselves."""
    if ".dev" in dist.version or "+" in dist.version:
        return True
    try:
        return "dir_info" in json.loads(dist.read_text("direct_url.json") or "{}")
    except ValueError:
        return True


def route(prefix: str = sys.prefix, dist=None, nt: bool = os.name == "nt"):
    """(name, command, why): the upgrade command for this install, or name None and why it is only printed."""
    try:
        dist = dist or metadata.distribution("cloakroom")
    except metadata.PackageNotFoundError:
        dist = None
    s = spec(extras())
    pip = [sys.executable, "-m", "pip", "install", "-U", s]
    if dist is None or _dev(dist):
        return None, None, ("this copy was installed from a folder of Cloakroom's source code on your computer; update that "
                            "folder and install it from there again")
    if os.path.isfile(os.path.join(prefix, "uv-receipt.toml")):
        home_uv = os.path.expanduser("~/.local/bin/uv")  # where the plugin's installer puts uv when it fetches it
        uv = shutil.which("uv") or (home_uv if os.path.isfile(home_uv) else None)
        cmd = [uv or "uv", "tool", "install", "--force", "--python", "%d.%d" % sys.version_info[:2], s]
        name, why = ("uv", None) if uv else (None, "it was installed with uv (an installer), but uv can't be found here")
    elif os.path.isfile(os.path.join(prefix, "pipx_metadata.json")):
        cmd = ["pipx", "install", "--force", s]
        name, why = ("pipx", None) if shutil.which("pipx") else (None, "it was installed with pipx (an installer), but pipx can't be found here")
    elif (dist.read_text("INSTALLER") or "").strip() == "pip":
        cmd, name, why = pip, "pip", None
    else:
        return None, pip, ("can't tell which installer put Cloakroom here; if it wasn't pip, update it with the one you "
                           "used")
    if nt and name:
        return None, cmd, "on Windows a program can't replace itself while it is running"
    return name, cmd, why


def _show(cmd) -> str:
    return " ".join(f'"{c}"' if re.search(r"[\s\[\]]", c) else c for c in cmd)


def _codex_hooks():
    """Our Codex hook entries, to see whether the new version's text differs (Codex then asks for trust again)."""
    try:
        hooks = agents._load(os.path.join(agents.codex_home(), "hooks.json")).get("hooks", {})
    except (ValueError, OSError):
        return None
    return sorted(json.dumps(h, sort_keys=True) for gs in hooks.values() if isinstance(gs, list) for g in gs
                  if isinstance(g, dict) for h in g.get("hooks") or [] if agents._ours(h))


def _git_hook_here() -> bool:
    from . import githook
    try:
        d = githook.hooks_dir(".")
    except (ValueError, OSError):
        return False
    return any(githook._ours(os.path.join(d, n)) for n in githook.HOOKS)


def installed() -> list[str]:
    """What Cloakroom installed for other agents on this machine (and this repo's git hook)."""
    return ([a for a, on in (("codex", agents.codex_installed()), ("gemini", agents.gemini_installed()),
                         ("cursor", agents.cursor_installed())) if on]
            + (["git-hook"] if _git_hook_here() else []))


def run(check: bool = False) -> int:
    name, cmd, why = route()
    redo = installed()
    print(f"Cloakroom {__version__}, optional parts installed: {', '.join(extras()) or 'none'} (these are kept)")
    if not name:
        print(f"Nothing was changed: {why}.")
        steps = ([_show(cmd)] if cmd else []) + [f"cloakroom install {a}" for a in redo]
        if steps:
            print("To update it yourself, run these in a terminal, one at a time:")
        for i, step in enumerate(steps, 1):
            print(f"  {i}. {step}")
        print(CLAUDE_NOTE)
        return 1
    if check:
        print("Only checking, nothing is changed. Without --check, cloakroom update would:")
        print(f"  update Cloakroom with {name}, the installer you used: {_show(cmd)}")
        for a in redo:
            print(f"  then set up Cloakroom's checks for {NAMES[a]} again: cloakroom install {a}")
        if not redo:
            print("  nothing more: Cloakroom isn't set up for Codex, Gemini CLI, Cursor's command-line agent or git commits in this folder")
        print(CLAUDE_NOTE)
        return 0
    print(f"Updating Cloakroom with {name}, the installer you used: {_show(cmd)}", flush=True)
    if subprocess.run(cmd).returncode != 0:
        print(f"cloakroom: the update didn't work (the reason is above). Version {__version__} stays and keeps "
              "protecting you.", file=sys.stderr)
        return 1
    program = agents._program() or "cloakroom"
    try:
        new = subprocess.run([program, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        new = ""
    print(f"Installed now: {new or 'a version Cloakroom could not read (run cloakroom --version)'}")
    rc = 0
    for a in redo:  # written by the new program, so the hook text matches its version
        before = _codex_hooks() if a == "codex" else None
        print(f"Setting up Cloakroom's checks for {NAMES[a]} again: cloakroom install {a}", flush=True)
        rc |= subprocess.run([program, "install", a]).returncode
        if a == "codex" and _codex_hooks() != before:
            print("Codex: Cloakroom's checks changed, and Codex skips changed checks until you approve them. Start "
                  "codex, type /hooks, and trust each Cloakroom entry (trust is Codex's word for approve).")
    if "git-hook" in redo:
        print("Git commit checks in your other projects keep working. To bring one up to date, open that "
              "project's folder in a terminal and run: cloakroom install git-hook")
    from . import client, config
    h, ver = client.health(config.load()["port"]) or {}, new.split()[-1] if new else None
    if h.get("service") == "cloakroom" and ver and h.get("version") != ver:
        print(f"Cloakroom's background helper is still running version {h.get('version')}. It switches to the new "
              "version when it restarts: restart your computer, or quit the program named 'cloakroom serve' (it "
              "starts again by itself when needed).")
    print(CLAUDE_NOTE)
    return rc
