"""File log: where restored real values were written on this machine, so later reads mask them again, in the same
session and in later ones, even where detection alone would miss them.

When a local write (Write, Edit, MultiEdit, NotebookEdit, a local shell command) gets real values back for
placeholders, the pre hook notes the placeholders and paths (pending/, no values) and the post hook, once the tool
has run, finds those values in the files and records per file: the path, its SHA-256, size and mtime, and per value
the byte span, the SHA-256 of the value, its kind and placeholder. Never the value: it is read back from the file
when needed. The log (filelog.bin) is AES-GCM encrypted like the vault.
Before detection runs on later output, the live entries for files under the session's working folder (and the file
being read) give their values as exact matches, masked to the session's placeholder for that value.
An entry lives while the file still matches: the same hash, or a span that still holds the same value. A deleted or
changed file drops it. At most MAX_SPANS spans and MAX_BYTES of log, oldest files dropped first.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import ntpath
import os
import time

from . import config

MAX_SPANS = 5000
MAX_BYTES = 10 * 1024 * 1024
MAX_FILE = 4 * 1024 * 1024  # bigger files aren't logged or read back
MIN_VALUE = 4  # shorter values would turn up by chance
PENDING_SECONDS = 3600
_CACHE: dict = {"key": None, "files": {}}  # the decrypted log, by the log file's (mtime_ns, size)
_VALUES: dict = {}  # (path, size, mtime_ns) -> {value: kind}, read back from the file; in memory only


def _path():
    return config.private_dir() / "filelog.bin"


def norm(path: str, cwd: str = "", windows: bool | None = None) -> str:
    """One spelling per file: absolute, symlinks resolved; on Windows lower case, forward slashes, no \\\\?\\ prefix."""
    windows = os.name == "nt" if windows is None else windows
    path = os.path.expanduser(str(path))
    if windows:
        path = path.replace("/", "\\")
        if path.startswith("\\\\?\\UNC\\"):
            path = "\\\\" + path[8:]
        elif path.startswith("\\\\?\\"):
            path = path[4:]
        if cwd and not ntpath.isabs(path):
            path = ntpath.join(cwd, path)
        return ntpath.normcase(ntpath.normpath(path)).replace("\\", "/")
    if cwd and not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.realpath(path)


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/\\") + "/") or path.startswith(root.rstrip("/\\") + os.sep)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: str) -> bytes | None:
    try:
        if os.path.getsize(path) > MAX_FILE:
            return None
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _load() -> dict:
    p = _path()
    try:
        st = p.stat()
    except OSError:
        return {}
    key = (st.st_mtime_ns, st.st_size)
    if _CACHE["key"] != key:
        from . import crypto
        _CACHE["files"], _CACHE["key"] = json.loads(crypto.decrypt(p.read_bytes()))["files"], key
    return {k: dict(v) for k, v in _CACHE["files"].items()}


def _save(files: dict):
    from . import crypto
    files = _cap(files)
    p = _path()
    blob = crypto.encrypt(json.dumps({"files": files}).encode())
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(blob)
    os.replace(tmp, p)
    _CACHE["key"] = None


def _cap(files: dict) -> dict:
    """Oldest files first out, until the log fits MAX_SPANS and MAX_BYTES."""
    order = sorted(files, key=lambda k: files[k].get("ts", 0))
    spans = sum(len(e["spans"]) for e in files.values())
    size = len(json.dumps(files))
    while order and (spans > MAX_SPANS or size > MAX_BYTES):
        k = order.pop(0)
        spans -= len(files[k]["spans"])
        size -= len(json.dumps({k: files[k]}))
        del files[k]
    return files


def _check(path: str, entry: dict) -> tuple[dict | None, dict[str, str]]:
    """The entry as it holds now (None: gone) and its values {value: kind}, read back from the file."""
    try:
        st = os.stat(path)
    except OSError:
        return None, {}
    stamp = (path, st.st_size, st.st_mtime_ns)
    if st.st_size == entry["size"] and st.st_mtime_ns == entry["mtime"] and stamp in _VALUES:
        return entry, _VALUES[stamp]
    data = _read(path)
    if data is None:
        return None, {}
    if _sha(data) != entry["sha"]:  # changed: keep the spans that still hold their value
        spans = [s for s in entry["spans"] if _sha(data[s[0]:s[1]]) == s[2]]
        if not spans:
            return None, {}
        entry = {**entry, "spans": spans, "sha": _sha(data)}
    entry = {**entry, "size": st.st_size, "mtime": st.st_mtime_ns}
    values = {}
    for a, b, _, kind, _ in entry["spans"]:
        with contextlib.suppress(UnicodeDecodeError):
            values[data[a:b].decode("utf-8")] = kind
    _VALUES[stamp] = values
    return entry, values


def known(roots: list[str], read_path: str = "") -> dict[str, str]:
    """{value: kind} of logged values in files under roots (or the file being read). Values stay in memory."""
    files = _load()
    if not files:
        return {}
    roots = [norm(r) for r in roots if r]
    read_path = norm(read_path) if read_path else ""
    out, updates = {}, {}
    for path, entry in files.items():
        if not (path == read_path or any(_under(path, r) for r in roots)):
            continue
        new, values = _check(path, entry)
        if new != entry:
            updates[path] = new
        out.update(values)
    if updates:  # a file changed or went: write that back (under the lock, onto the log as it is now)
        from . import events
        with events._locked("filelog.lock"):
            current = _load()
            for path, new in updates.items():
                if new is None:
                    current.pop(path, None)
                elif path in current:
                    current[path] = new
            _save(current)
    return out


def record(path: str, values: list[tuple[str, str, str]]) -> int:
    """Log where (value, kind, placeholder) sit in the file at path. Returns the spans now logged for it."""
    data = _read(path)
    if data is None:
        return 0
    from . import events
    key = norm(path)
    with events._locked("filelog.lock"):
        files = _load()
        for p in list(files):  # prune on every write: files gone or changed
            new, _ = _check(p, files[p])
            if new is None:
                del files[p]
            else:
                files[p] = new
        spans = {tuple(s[:2]): s for s in (files.get(key) or {}).get("spans", [])}
        for value, kind, ph in values:
            raw = value.encode("utf-8")
            if len(value) < MIN_VALUE:
                continue
            i = data.find(raw)
            while i >= 0:
                spans[(i, i + len(raw))] = [i, i + len(raw), _sha(raw), kind, ph]
                i = data.find(raw, i + len(raw))
        if not spans:
            return 0
        st = os.stat(path)
        files[key] = {"sha": _sha(data), "size": st.st_size, "mtime": st.st_mtime_ns, "ts": time.time(),
                      "spans": sorted(spans.values())}
        _save(files)
        return len(spans)


def _pending_file(tool_use_id: str):
    safe = "".join(c for c in tool_use_id if c.isalnum() or c in "-_")[:80] or "default"
    return config.private_dir("pending") / f"{safe}.json"


def pending(tool_use_id: str, paths: list[str], placeholders: list[str]):
    """Before a restoring write runs: which placeholders went into which paths. No values."""
    d = config.private_dir("pending")
    now = time.time()
    for old in d.iterdir():  # a tool that never ran (denied, interrupted) leaves one behind
        with contextlib.suppress(OSError):
            if now - old.stat().st_mtime > PENDING_SECONDS:
                old.unlink()
    p = _pending_file(tool_use_id)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"paths": paths, "placeholders": sorted(set(placeholders))}, f)


def complete(tool_use_id: str, vault) -> int:
    """After the write ran: log the spans of the restored values in its files. Returns spans logged."""
    p = _pending_file(tool_use_id)
    try:
        job = json.loads(p.read_text())
    except (OSError, ValueError):
        return 0
    p.unlink(missing_ok=True)
    values = [(vault.by_placeholder[ph], ph.split("_")[1], ph) for ph in job["placeholders"] if ph in vault.by_placeholder]
    n = 0
    for path in job["paths"]:
        if values and os.path.isfile(path):
            n += record(path, values)
    return n
