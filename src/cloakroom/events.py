"""Event log for the dashboard and `cloakroom report`: one JSON line per hook call. Never values or text.

Each event: id, ts, session, event, tool, target, kinds ({kind: count}), placeholders, action, ms, prev.
  target is set only when something was found: the file path for file tools (masked like any output), the program
  name for shell commands (never its arguments), else "". placeholders (PII_EMAIL_1...) let the dashboard look the
  values up in the session vault when you choose to reveal them.
  prev is the SHA-256 of the line before it ("start" on the chain's first line), so a changed or removed line breaks
  the chain (`cloakroom report --verify`). Tamper-evident, not tamper-proof: whoever rewrites every later line can
  rebuild it, which is why --verify prints the head hash for you to keep.
One file per month (events-YYYY-MM.jsonl), kept for log_months months (config, default 12). Files from before 0.6.0
(events.jsonl, events.1.jsonl) are read as they are and never rewritten; the chain starts fresh after them.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from . import config

PATH_KEYS = ("file_path", "notebook_path", "path")
LEGACY = ("events.jsonl", "events.1.jsonl")
_MONTH = re.compile(r"events-(\d{4}-\d{2})\.jsonl")


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


def months() -> list[Path]:
    """The monthly files, oldest first."""
    if not config.HOME.exists():
        return []
    return sorted(p for p in config.HOME.iterdir() if _MONTH.fullmatch(p.name))


def line_hash(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _last_line(path: Path) -> str:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 65536))
        tail = f.read().decode("utf-8", "replace").rstrip("\n")
    return tail.rsplit("\n", 1)[-1]


def head() -> str:
    """The hash of the newest line, "start" when there is none yet."""
    for path in reversed(months()):
        line = _last_line(path)
        if line:
            return line_hash(line)
    return "start"


@contextlib.contextmanager
def _locked(name: str = "events.lock"):
    """One writer at a time: parallel hooks would otherwise read the same head and fork the chain (also used by
    filelog.py, with its own lock file)."""
    fd = os.open(config.private_dir() / name, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            for _ in range(200):
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            with contextlib.suppress(OSError):
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)


def _keep_months() -> int:
    try:
        return max(1, int(config.load().get("log_months", 12)))
    except Exception:
        return 12


def _prune(now: str) -> list[str]:
    """Delete monthly files older than log_months; returns the months deleted."""
    y, m = map(int, now.split("-"))
    total = y * 12 + m - 1 - (_keep_months() - 1)
    oldest = f"{total // 12:04d}-{total % 12 + 1:02d}"
    gone = []
    for path in months():
        month = _MONTH.fullmatch(path.name).group(1)
        if month < oldest:
            path.unlink(missing_ok=True)
            gone.append(month)
    return gone


def _append(ev: dict) -> dict:
    path = config.private_dir() / f"events-{ev['ts'][:7]}.jsonl"
    ev["prev"] = head()
    line = json.dumps(ev)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(line + "\n")
    return ev


def record(event: str, data, action: str, found: list[tuple[str, str]], ms: float, target: str = "",
           extra: dict | None = None) -> dict:
    data = data if isinstance(data, dict) else {}
    tool = data.get("tool_name", "")
    placeholders = list(dict.fromkeys(p for p, _ in found if p.startswith("PII_")))
    ev = {"id": secrets.token_hex(6), "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
          "session": str(data.get("session_id", "default"))[:80], "event": event, "tool": tool,
          "target": target, "kinds": counts([k for _, k in found]), "placeholders": placeholders, "action": action,
          "ms": round(ms, 1), **(extra or {})}
    with _locked():
        if not (config.HOME / f"events-{ev['ts'][:7]}.jsonl").exists():  # a new month: drop the oldest
            for month in _prune(ev["ts"][:7]):
                _append({"id": secrets.token_hex(6), "ts": ev["ts"], "session": "", "event": "log", "tool": "",
                         "target": f"events-{month}.jsonl", "kinds": {}, "placeholders": [], "action": "pruned",
                         "ms": 0.0})
        return _append(ev)


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def read(limit: int = 500) -> list[dict]:
    """Newest first: the monthly files, then the files from before 0.6.0."""
    out = []
    paths = list(reversed(months())) + [config.HOME / n for n in LEGACY]
    for path in paths:
        for line in reversed(_lines(path)):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
            if len(out) >= limit:
                return out
    return out


def all_events() -> list[dict]:
    """Oldest first, every file (for the report)."""
    return list(reversed(read(limit=10 ** 9)))


def verify() -> dict:
    """Walk the chain oldest to newest. {"ok", "lines", "head", "anchor", "broken": "file:line" or ""}.
    The oldest kept line is the anchor (older months may have been pruned); every later line must name the hash
    of the one before it."""
    prev, n, anchor = None, 0, ""
    for path in months():
        for i, line in enumerate(_lines(path), 1):
            try:
                ev = json.loads(line)
            except ValueError:
                return {"ok": False, "lines": n, "head": prev or "start", "anchor": anchor, "broken": f"{path.name}:{i}"}
            if prev is None:
                anchor = str(ev.get("prev", ""))
            elif ev.get("prev") != prev:
                return {"ok": False, "lines": n, "head": prev, "anchor": anchor, "broken": f"{path.name}:{i}"}
            prev, n = line_hash(line), n + 1
    return {"ok": True, "lines": n, "head": prev or "start", "anchor": anchor, "broken": ""}
