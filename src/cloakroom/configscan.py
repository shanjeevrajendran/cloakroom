"""cloakroom scan, and the post-checkout / post-merge git hooks: the commands a repo's agent config would run.

Every coding agent asks before trusting a new folder; none asks again when a folder you already trusted changes. A
pull can add a SessionStart hook or an MCP server that runs the next time you open the agent there. This lists those
commands (Claude Code and Gemini CLI hooks and MCP servers, Codex project config, VS Code tasks) and, after a pull,
checkout or merge, says which ones were added or changed. It only reads and warns: it never blocks or edits.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

# shortcut: a fixed list of project config files; add a file here when another agent reads project config
FILES = (".claude/settings.json", ".claude/settings.local.json", ".mcp.json", ".gemini/settings.json",
         ".codex/hooks.json", ".codex/config.toml", ".vscode/tasks.json")
ZERO = "0" * 40


def _jsonc(text: str):
    """JSON with // and /* */ comments and trailing commas (VS Code's tasks.json allows both)."""
    text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', lambda m: m.group(1) or "", text, flags=re.S)
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))


def _line(text: str, needle: str) -> int:
    """1-based line of the first line holding needle as written in the file (JSON-escaped), else 0."""
    for forms in (json.dumps(needle)[1:-1], needle):
        for i, line in enumerate(text.splitlines(), 1):
            if forms in line:
                return i
    return 0


def _cmd(entry: dict) -> str:
    return " ".join(str(x) for x in [entry.get("command", ""), *(entry.get("args") or [])] if x != "").strip()


def commands(path: str, text: str) -> list[tuple[str, str, int]]:
    """(what, command, line) for every command this config file would run. Unreadable files give an entry saying so,
    so a broken file after a pull is still reported rather than silently empty."""
    out = []
    if path.endswith(".toml"):  # Codex: [mcp_servers.x] command = "...", read line by line (no tomllib on 3.10)
        section = ""
        for i, line in enumerate(text.splitlines(), 1):
            if m := re.match(r"\s*\[+\s*([^\]]+?)\s*\]+", line):
                section = m.group(1)
            elif m := re.match(r"\s*(command|args)\s*=\s*(.+?)\s*$", line):
                out.append((f"{section or 'config'} {m.group(1)}", m.group(2), i))
        return out
    try:
        data = _jsonc(text)
    except ValueError:
        return [("unreadable JSON", "", 1)]
    if not isinstance(data, dict):
        return out
    hooks = data.get("hooks")
    for event, groups in (hooks.items() if isinstance(hooks, dict) else ()):
        for group in groups if isinstance(groups, list) else ():
            for h in (group.get("hooks") or []) if isinstance(group, dict) else ():
                if isinstance(h, dict) and h.get("command"):
                    out.append((f"{event} hook", h["command"], _line(text, h["command"])))
    if isinstance(data.get("statusLine"), dict) and data["statusLine"].get("command"):
        out.append(("statusLine", data["statusLine"]["command"], _line(text, data["statusLine"]["command"])))
    servers = data.get("mcpServers")
    for name, s in (servers.items() if isinstance(servers, dict) else ()):
        if isinstance(s, dict) and (s.get("command") or s.get("url")):
            cmd = _cmd(s) or s.get("url", "")
            out.append((f"MCP server {name}", cmd, _line(text, s.get("command") or s["url"])))
    for t in (data.get("tasks") or []) if path.endswith("tasks.json") else []:
        if isinstance(t, dict) and t.get("command"):
            auto = (t.get("runOptions") or {}).get("runOn") == "folderOpen"
            out.append((f"task {t.get('label', '')!r}" + (" (runs on folder open)" if auto else ""), _cmd(t),
                        _line(text, t["command"])))
    return out


def _flags(found: dict) -> list[str]:
    """The Tensorlake worm's shape: an agent hook running a file in .vscode/ and a folder-open task running a file in
    .claude/, each pointing at the other's folder. Any folder-open task is worth a line on its own."""
    hooks = [(p, w, c, n) for p, rows in found.items() if not p.startswith(".vscode/")
             for w, c, n in rows if "hook" in w and ".vscode/" in c]
    tasks = [(p, w, c, n) for p, rows in found.items() if p.startswith(".vscode/") for w, c, n in rows]
    cross = [(p, w, c, n) for p, w, c, n in tasks if ".claude/" in c or ".gemini/" in c or ".codex/" in c]
    flags = []
    if hooks and cross:
        flags.append("WORM SHAPE: an agent hook runs a file in .vscode/ and a VS Code task runs a file in the agent's "
                     "folder, each pointing at the other (as in the Tensorlake npm worm): "
                     + "; ".join(f"{p}:{n} {c}" for p, _, c, n in hooks + cross))
    flags += [f"runs on folder open: {p}:{n} {c}" for p, w, c, n in tasks if "folder open" in w]
    return flags


def _working(root: str, path: str):
    try:
        with open(os.path.join(root, path), encoding="utf-8-sig", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def collect(read) -> dict:
    """{file: [(what, command, line)]} for each config file read(path) returns text for."""
    return {p: commands(p, t) for p in FILES if (t := read(p)) is not None}


def scan(root: str = ".") -> int:
    """cloakroom scan <dir>: print every command the agent config in root would run, and the flags. Exit 1 on a flag."""
    found = collect(lambda p: _working(root, p))
    rows = [(p, w, c, n) for p, r in found.items() for w, c, n in r]
    if not rows:
        print(f"No agent config commands in {root} (checked: {', '.join(FILES)})")
    for p, w, c, n in rows:
        print(f"{p}:{n}  {w}: {c}")
    flags = _flags(found)
    for f in flags:
        print(f"cloakroom: {f}")
    return 1 if flags else 0


def changes(before: dict, after: dict) -> list[str]:
    """Commands that are new or whose text changed, as 'file:line what: command' lines. Removed ones are not news."""
    out = []
    for p, rows in after.items():
        old = {(w, c) for w, c, _ in before.get(p, [])}
        old_whats = {w for w, _ in old}
        for w, c, n in rows:
            if (w, c) not in old:
                out.append(f"{p}:{n} {'changed' if w in old_whats else 'new'} {w}: {c}")
    return out


def _show(rev: str, path: str):
    r = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True, timeout=10,
                       encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else None


def check(kind: str, args: list[str]) -> int:
    """The post-checkout / post-merge hook: warn on stderr when the agent config gained or changed a command. Always
    exit 0: git hands a post-checkout hook's exit code back as checkout's own, and this never stops anything."""
    try:
        if kind == "checkout":  # args: previous HEAD, new HEAD, 1 for a branch checkout (0: single files)
            if len(args) < 3 or args[2] != "1" or args[0] == args[1]:
                return 0
            old, new = args[0], args[1]
        else:  # post-merge (a pull too): before is ORIG_HEAD
            old, new = "ORIG_HEAD", "HEAD"
        before = {} if old == ZERO else collect(lambda p: _show(old, p))
        after = collect(lambda p: _show(new, p))
        found = changes(before, after)
        flags = _flags(after) if found else []
    except Exception as e:  # noqa: BLE001 - a warning that breaks never breaks your checkout
        print(f"cloakroom: agent config not compared after this {kind} ({type(e).__name__})", file=sys.stderr)
        return 0
    if found:
        print(f"cloakroom: this {kind} added or changed commands your coding agents or editor run in this repo. "
              "Read them before you next open Claude Code, Codex, Gemini CLI or VS Code here:", file=sys.stderr)
        for line in found + flags:
            print(f"  {line}", file=sys.stderr)
        print("  (warning only; nothing was blocked or changed. All of them: cloakroom scan .)", file=sys.stderr)
    return 0
