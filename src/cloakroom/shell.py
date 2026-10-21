"""Bash safeguards: decide whether a command may get real values back, and put them back safely quoted."""
from __future__ import annotations

import re
import shlex

from .vault import PLACEHOLDER

# Commands that can send data off this machine. Placeholders stay; the user is asked.
NETWORK = {"curl", "wget", "http", "https", "httpie", "nc", "ncat", "netcat", "telnet", "ssh", "scp", "sftp", "rsync",
           "ftp", "mail", "sendmail", "mutt", "gh", "aws", "gcloud", "az", "kubectl", "helm", "psql", "mysql",
           "mongosh", "redis-cli", "openssl", "socat", "ping", "dig", "nslookup", "host", "whois"}
NETWORK_SUB = {"git": {"push", "fetch", "pull", "clone", "remote", "send-email", "ls-remote", "submodule"},
               "npm": {"publish", "login", "adduser"}, "docker": {"push", "login", "pull", "run"},
               "pip": {"install", "download"}, "yarn": {"publish", "npm"}}
# Commands that only read or write local files and print locally.
LOCAL = {"cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "ag", "sed", "awk", "sort", "uniq",
         "wc", "cut", "tr", "diff", "cmp", "comm", "ls", "find", "echo", "printf", "test", "[", "jq", "yq", "mkdir",
         "cp", "mv", "touch", "rm", "chmod", "ln", "stat", "file", "basename", "dirname", "realpath", "tee",
         "pwd", "cd", "true", "false", "column", "nl", "fold", "md5", "shasum", "sha256sum"}
LOCAL_SUB = {"git": {"status", "log", "diff", "show", "grep", "add", "commit", "blame", "checkout", "branch", "switch",
                     "restore", "stash", "rev-parse", "ls-files", "mv", "rm", "tag"}}
_SEGMENT_SPLIT = re.compile(r"\|\||&&|[|;&\n]|\$\(|`")

# PowerShell: cmdlets and aliases are case-insensitive.
PS_NETWORK = {"invoke-webrequest", "iwr", "invoke-restmethod", "irm", "curl", "wget", "start-bitstransfer",
              "send-mailmessage", "test-netconnection", "tnc", "test-connection", "resolve-dnsname",
              "invoke-command", "enter-pssession", "new-pssession", "copy-item -tosession"} | NETWORK
PS_NETWORK_CODE = re.compile(r"(?i)net\.webclient|system\.net\.(?:http|sockets|webclient|mail)|tcpclient|udpclient|"
                             r"\[net\.|webrequest|downloadstring|uploadstring|uploadfile")
PS_LOCAL = {"get-content", "gc", "cat", "type", "select-string", "sls", "get-childitem", "gci", "ls", "dir",
            "set-content", "sc", "add-content", "ac", "out-file", "write-output", "echo", "write-host",
            "measure-object", "measure", "sort-object", "sort", "where-object", "where", "?", "foreach-object", "%",
            "select-object", "select", "copy-item", "cp", "copy", "move-item", "mv", "move", "remove-item", "rm", "del",
            "new-item", "ni", "mkdir", "md", "test-path", "get-item", "gi", "resolve-path", "split-path", "join-path",
            "compare-object", "diff", "findstr", "get-filehash", "format-table", "ft", "format-list", "fl",
            "out-string", "get-location", "pwd", "set-location", "cd", "group-object", "convertto-json",
            "convertfrom-json", "convertto-csv", "convertfrom-csv", "import-csv", "export-csv"}
_PS_SEGMENT_SPLIT = re.compile(r"\|\||&&|[|;\n]|\$\(")


def _first_words(segment: str, powershell: bool = False) -> tuple[str, str]:
    try:
        words = segment.split() if powershell else shlex.split(segment, comments=True)
    except ValueError:
        words = segment.split()
    words = [w.strip("\"'&(") for w in words]
    while words and re.fullmatch(r"\w+=.*", words[0]):  # leading VAR=value
        words = words[1:]
    if words and words[0] in ("sudo", "env", "time", "nice", "command", "exec", "xargs"):
        words = words[1:]
    if not words:
        return "", ""
    cmd = re.split(r"[\\/]", words[0])[-1]
    cmd = cmd[:-4] if cmd.lower().endswith(".exe") else cmd
    return (cmd.lower() if powershell else cmd), (words[1] if len(words) > 1 else "")


def _scan(command: str, powershell: bool = False) -> tuple[str, str]:
    """(verdict, the first network program found or "")."""
    if "/dev/tcp/" in command or "/dev/udp/" in command:
        return "network", "/dev/tcp"
    if powershell and PS_NETWORK_CODE.search(command):
        return "network", "a .NET network call"
    network, local = (PS_NETWORK, PS_LOCAL | LOCAL) if powershell else (NETWORK, LOCAL)
    verdict = "local"
    for seg in (_PS_SEGMENT_SPLIT if powershell else _SEGMENT_SPLIT).split(command):
        if not seg.strip():
            continue
        cmd, sub = _first_words(seg, powershell)
        if not cmd:
            continue
        if cmd in network:
            return "network", cmd
        if sub in NETWORK_SUB.get(cmd, ()):
            return "network", f"{cmd} {sub}"
        if not (cmd in local or sub in LOCAL_SUB.get(cmd, ())):
            verdict = "unknown"
    return verdict, ""


def classify(command: str, powershell: bool = False) -> str:
    """'network' if any part can send data out, 'local' if every part is known-local, else 'unknown'."""
    return _scan(command, powershell)[0]


# Credential headers and parameters. A value that is a variable ($TOKEN, ${TOKEN}, $env:TOKEN) or a placeholder
# is not a literal: Cloakroom can't see it, and the shell fills it in.
_AUTH = re.compile(r"(?i)(?P<name>authorization|proxy-authorization|x-api-key|api[-_]?key|x-auth-token|"
                   r"(?:access[-_]?|auth[-_]?)?token)\s*(?P<sep>[:=])\s*(?:(?:bearer|basic|token)\s+)?"
                   r"(?P<value>[^\s'\"&;|)]+)")
LITERAL_MIN = 16


def auth_headers(command: str) -> list[tuple[str, str]]:
    """(where, value) for each credential header or token= parameter: where is "Authorization header",
    "token= parameter"..."""
    return [(f"{m.group('name')} header" if m.group("sep") == ":" else f"{m.group('name')}= parameter", m.group("value"))
            for m in _AUTH.finditer(command)]


def literal_tokens(command: str) -> list[str]:
    """Where credential headers or parameters carry a literal token of LITERAL_MIN+ characters (not a variable, not a
    placeholder). Never the value."""
    out = []
    for name, value in auth_headers(command):
        if len(value) < LITERAL_MIN or "$" in value or "%" in value and re.search(r"%\w+%", value) or PLACEHOLDER.search(value):
            continue
        out.append(name)
    return out


def egress(command: str, powershell: bool = False) -> str:
    """How a command could send data off this machine ("curl", "git push", "an Authorization header"), or ""."""
    verdict, via = _scan(command, powershell)
    if verdict == "network":
        return via
    headers = auth_headers(command)
    return f"a {headers[0][0]}" if headers else ""


def _quote_for(value: str, state: str, powershell: bool = False) -> str:
    if powershell:  # single quotes double up; inside double quotes the escape character is a backtick
        if state == "'":
            return value.replace("'", "''")
        if state == '"':
            return re.sub(r'([`"$])', r"`\1", value)
        return "'" + value.replace("'", "''") + "'"
    if state == "'":
        return value.replace("'", "'\\''")
    if state == '"':
        return re.sub(r'([\\"$`])', r"\\\1", value)
    return shlex.quote(value)


def restore(command: str, lookup, powershell: bool = False) -> tuple[str, list[str]]:
    """Swap known placeholders for their values, quoted for where each one sits. Returns (command, unknown)."""
    out, unknown, i, state = [], [], 0, ""
    escape = "`" if powershell else "\\"
    while i < len(command):
        c = command[i]
        if state != "'" and c == escape and i + 1 < len(command):
            out.append(command[i:i + 2])
            i += 2
            continue
        if c in ("'", '"') and (state == "" or state == c):
            state = "" if state == c else c
            out.append(c)
            i += 1
            continue
        m = PLACEHOLDER.match(command, i)
        if m and (i == 0 or not (command[i - 1].isalnum() or command[i - 1] == "_")):
            value = lookup(m.group())
            if value is None:
                unknown.append(m.group())
                out.append(m.group())
            else:
                out.append(_quote_for(value, state, powershell))
            i = m.end()
            continue
        out.append(c)
        i += 1
    return "".join(out), unknown
