"""Outgoing-data control (1.0): where a call sends data (the ledger), the approved-sites list, and the header guard's
inputs. Pure functions; hook.py holds or passes. Hosts and data kinds only, never values.

shortcut: the host of a command is read from URLs, user@host words, the first host-like word of ssh-family programs
and the remote of git push/fetch/pull. Anything else that sends data (aws, npm publish, docker pull) is "(unresolved)":
logged as such and, with the approved-sites list on, held. Upgrade when a program is common enough to teach."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from . import shell
from .vault import ANY_PLACEHOLDER

SEARCH = "web-search"  # WebSearch has no host of its own: the ledger and the list name it like one
UNRESOLVED = "(unresolved)"
LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}  # never leaves this machine: not logged, never held
PERSON_KINDS = {"EMAIL", "PHONE", "PERSON"}  # what the header guard holds (the rest is secrets, held elsewhere)

_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s'\"<>)\]}]+")
_SCP = re.compile(r"(?<![\w.-])[\w.+-]+@([A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]):(?!//)")  # git@github.com:org/repo
_HEADER = re.compile(r"""(?i)(?:\s-H|--header|-Headers)[\s=]+("[^"]*"|'[^']*'|@\{[^}]*\}|\S+)""")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*")
_PATTERN = re.compile(r"(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")

# Programs whose first host-like word is the destination, and the ones that also need a dot in it (a web client's
# word could be a file name).
HOST_ARG = {"ssh", "scp", "sftp", "rsync", "nc", "ncat", "netcat", "telnet", "ftp", "ping", "dig", "nslookup", "host",
            "whois", "socat", "traceroute"}
WEB_CLIENT = {"curl", "wget", "http", "https", "httpie", "iwr", "irm", "invoke-webrequest", "invoke-restmethod"}
_COLON = {"scp", "rsync"}  # only a word with host: in it is remote
_VALUE_OPTS = {"-i", "-p", "-P", "-o", "-l", "-F", "-J", "-L", "-R", "-D", "-b", "-c", "-e", "-W", "-w", "-u", "-H",
               "-d", "-X", "-A", "-T", "-m", "--output", "--data", "--header", "--user", "--max-time"}
_GIT_REMOTE_SUBS = {"push", "fetch", "pull", "ls-remote"}


def own_username() -> str:
    """The account name from the platform (the password database, or GetUserNameW on Windows), never $USER, which the
    agent's environment can set."""
    try:
        if os.name == "nt":
            import ctypes
            n = ctypes.c_uint32(257)
            buf = ctypes.create_unicode_buffer(257)
            if ctypes.windll.advapi32.GetUserNameW(buf, ctypes.byref(n)):
                return buf.value
        else:
            import pwd
            return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        pass
    return Path.home().name


def host_of(authority: str) -> str:
    """The lower-case host of a URL's authority part: no user@, port or trailing dot."""
    a = authority.rsplit("@", 1)[-1]
    h = a[1:].split("]")[0] if a.startswith("[") else a.split(":")[0]
    return h.lower().rstrip(".")


def _urls(text: str) -> list[str]:
    return [host_of(m.group().split("://", 1)[1].split("/")[0].split("?")[0].split("#")[0]) for m in _URL.finditer(text)]


def _words(segment: str, powershell: bool) -> list[str]:
    try:
        words = shlex.split(segment, posix=not powershell)
    except ValueError:
        words = segment.split()
    words = [w.strip("\"'") for w in words]
    while words and (re.fullmatch(r"\w+=.*", words[0]) or words[0] in ("sudo", "env", "command", "exec", "time")):
        words = words[1:]
    return words


def _host_word(word: str, prog: str) -> str:
    if prog in _COLON and ":" not in word:
        return ""
    name = word.lstrip("@").rsplit("@", 1)[-1].split(":")[0].split("/")[0]
    if not _WORD.fullmatch(name) or len(name) < 2 or name.isdigit():  # one letter is a Windows drive (C:\...)
        return ""
    if prog in WEB_CLIENT and name not in LOOPBACK and not re.search(r"\.[A-Za-z]{2,}$|^\d+(\.\d+){3}$", name):
        return ""
    return name.lower().rstrip(".")


def _git_remote(words: list[str], cwd: str) -> list[str] | None:
    """The hosts of the remote a git push, fetch or pull talks to; None when git can't say."""
    i = 1
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in ("-C", "-c") else 1
    rest = [w for w in words[i + 1:] if not w.startswith("-")]
    name = rest[0] if rest else "origin"
    url = name if re.search(r"://|@", name) else ""
    if not url:
        try:
            r = subprocess.run(["git", "-C", cwd or ".", "remote", "get-url", name], capture_output=True, timeout=1)
        except Exception:
            return None
        if r.returncode != 0:
            return None
        url = r.stdout.decode("utf-8", "replace").strip()
    return _urls(url) + [m.group(1).lower() for m in _SCP.finditer(url)]  # a local path has no host: []


def command_hosts(command: str, powershell: bool = False, cwd: str = "") -> list[str]:
    """Where a command that sends data sends it: hosts in the order they appear, [UNRESOLVED] when none can be read,
    [] when it only talks to this machine. The caller has already checked shell.egress(command)."""
    found = _urls(command) + [m.group(1).lower() for m in _SCP.finditer(command)]
    network = remote_segments = 0
    for seg in (shell._PS_SEGMENT_SPLIT if powershell else shell._SEGMENT_SPLIT).split(command):
        words = _words(seg, powershell)
        if not words:
            continue
        prog = re.split(r"[\\/]", words[0])[-1].lower()
        prog = prog[:-4] if prog.endswith(".exe") else prog
        network += shell._scan(seg, powershell)[0] == "network"
        if prog == "git":
            sub = next((w for w in words[1:] if not w.startswith("-")), "")
            if sub == "remote":
                remote_segments += 1
            elif sub in _GIT_REMOTE_SUBS:
                hosts = _git_remote(words, cwd)
                found += hosts if hosts is not None else [UNRESOLVED]
        elif prog in HOST_ARG | WEB_CLIENT:
            skip = False
            for w in words[1:]:
                if skip or w.startswith("-"):
                    skip = w in _VALUE_OPTS
                    continue
                if h := _host_word(w, prog):
                    found.append(h)
                    break
    if network and network == remote_segments and not found:
        return []  # only `git remote`: it names remotes, it doesn't send anything
    real = list(dict.fromkeys(h for h in found if h))
    if real and all(h in LOOPBACK for h in real):
        return []
    return [h for h in real if h not in LOOPBACK] or [UNRESOLVED]


def _mcp_servers(cwd: str) -> dict:
    out = {}
    for path in (Path(cwd or ".") / ".mcp.json", Path.home() / ".claude.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.update(data.get("mcpServers") or {})
        out.update(((data.get("projects") or {}).get(cwd) or {}).get("mcpServers") or {})
    return {re.sub(r"[^A-Za-z0-9_-]", "_", k): v for k, v in out.items() if isinstance(v, dict)}


def tool_hosts(tool: str, args, cwd: str = "") -> list[str]:
    """Where a WebFetch, WebSearch or MCP call goes. An MCP server the config doesn't name (a plugin, a connector) is
    "mcp:<server>"; one started by a command is local, so only the addresses in its arguments count."""
    if tool == "WebSearch":
        return [SEARCH]
    urls = [h for h in _urls(json.dumps(args)) if h and h not in LOOPBACK]
    if tool == "WebFetch":
        return urls or [UNRESOLVED]
    if not tool.startswith("mcp__"):
        return []
    server = tool.split("__")[1] if tool.count("__") >= 2 else ""
    conf = _mcp_servers(cwd).get(server)
    if conf is None:
        return [f"mcp:{server}"] + urls
    own = [h for h in _urls(str(conf.get("url", ""))) if h and h not in LOOPBACK]
    return list(dict.fromkeys(own + urls))


def carried(blob: str, found: list[tuple[str, str]] = ()) -> dict[str, int]:
    """The data kinds a call carries, from the placeholders in it and the kinds Cloakroom found: counts, no values."""
    out: dict[str, int] = {}
    for ph in ANY_PLACEHOLDER.findall(blob):
        out[ph.split("_")[1]] = out.get(ph.split("_")[1], 0) + 1
    seen: dict[str, int] = {}
    for _, k in found:
        seen[k] = seen.get(k, 0) + 1
    return {k: max(out.get(k, 0), seen.get(k, 0)) for k in {*out, *seen}}


def normalize(entry: str) -> str:
    """A host or pattern as stored: lower case, no scheme, port or path. "" when it isn't a valid entry."""
    e = entry.strip().lower()
    if "://" in e:
        e = host_of(e.split("://", 1)[1].split("/")[0])
    e = e.rstrip(".")
    if re.fullmatch(r"mcp:[a-z0-9_-]+", e) or _PATTERN.fullmatch(e) and e not in ("*",):
        return e
    return ""


def listed(host: str, patterns: list[str]) -> bool:
    """Whether host is on the list: an exact name, or `*.example.com` for a site under example.com (not example.com)."""
    return any(host == p or p.startswith("*.") and host.endswith(p[1:]) for p in patterns)


def sent_parts(command: str) -> str:
    """What a command sends in the open: each URL (with its query string) and each header it sets. Not the body."""
    return "\n".join([m.group() for m in _URL.finditer(command)] + [m.group(1).strip("\"'") for m in _HEADER.finditer(command)])


def tool_parts(args) -> str:
    """The same for a web or MCP call: its address and headers."""
    if not isinstance(args, dict):
        return ""
    return "\n".join(json.dumps(v) if not isinstance(v, str) else v
                     for k, v in args.items() if k.lower() in ("url", "uri", "endpoint", "headers"))


def has_username(text: str) -> bool:
    name = own_username()
    return len(name) >= 3 and bool(re.search(rf"(?i)(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", text))
