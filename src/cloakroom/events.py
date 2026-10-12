"""Event log for the dashboard: one JSON line per hook call in ~/.cloakroom/events.jsonl. Never values or text.

Each event: id, ts, session, event, tool, target, kinds ({kind: count}), placeholders, action, ms.
  target is set only when something was found: the file path for file tools (masked like any output), the program
  name for shell commands (never its arguments), else "". placeholders (PII_EMAIL_1...) let the dashboard look the
  values up in the session vault when you choose to reveal them.
Rotates at MAX_BYTES to events.1.jsonl, so two files at most.
"""
from __future__ import annotations

import json
import os
import secrets
import time

from . import config

MAX_BYTES = 5 * 1024 * 1024
PATH_KEYS = ("file_path", "notebook_path", "path")


def target(tool: str, args) -> str:
    if not isinstance(args, dict):
        return ""
    if tool in ("Bash", "PowerShell"):
        words = str(args.get("command", "")).split()
        return os.path.basename(words[0]) if words else ""
    for k in PATH_KEYS:
        if isinstance(args.get(k), str):
            return args[k]
    return ""


def counts(kinds: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for k in kinds:
        out[k] = out.get(k, 0) + 1
    return out


def record(event: str, data, action: str, found: list[tuple[str, str]], ms: float, target: str = "") -> dict:
    data = data if isinstance(data, dict) else {}
    tool = data.get("tool_name", "")
    placeholders = list(dict.fromkeys(p for p, _ in found if p.startswith("PII_")))
    ev = {"id": secrets.token_hex(6), "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
          "session": str(data.get("session_id", "default"))[:80], "event": event, "tool": tool,
          "target": target, "kinds": counts([k for _, k in found]), "placeholders": placeholders, "action": action,
          "ms": round(ms, 1)}
    path = config.private_dir() / "events.jsonl"
    if path.exists() and path.stat().st_size > MAX_BYTES:
        os.replace(path, path.with_name("events.1.jsonl"))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps(ev) + "\n")
    return ev


def read(limit: int = 500) -> list[dict]:
    """Newest first, across the current and the rotated file."""
    out = []
    for name in ("events.jsonl", "events.1.jsonl"):
        path = config.HOME / name
        if not path.exists():
            continue
        for line in reversed(path.read_text().splitlines()):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
            if len(out) >= limit:
                return out
    return out
