"""Cursor's command-line agent: masking by changing the tool's input, because Cursor's hooks can't change a tool's
output (only an MCP tool's): https://cursor.com/docs/hooks. A file read is pointed at a masked copy of the file, and
a shell command is rewritten to run inside `cloakroom cursor-run`, which prints only masked output. hook.py's Cursor
branches call in here; agents.py has the translation of Cursor's JSON and the install.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from fnmatch import fnmatch
from pathlib import Path

from . import agents, client, config, hook, media, shell
from .vault import Vault

READ_MAX = 1024 * 1024  # a bigger file is denied, not copied (a PDF is read from disk and has no limit)
RUN_TTL = 600  # seconds a run record can be used
SWEEP_AFTER = 24 * 3600  # copies of a session that never ended
FLUSH_BYTES, FLUSH_IDLE, PEM_MAX = 64 * 1024, 1.0, 1024 * 1024  # runner: flush at, after this idle time, PEM limit
STREAM_MAX = 4 * 1024 * 1024  # shortcut: fixed cap, as GIT_MAX_TEXT; raise if real builds hit it
GRACE = 2.0  # seconds to wait for pipes a background child still holds, once the command itself has exited
READ_BIG = ("Cloakroom: {name} is over 1 MB, too large to check in one read. Read part of it with a shell command "
            "(head, sed -n '1,200p'): Cloakroom masks command output.")
READ_DENY = ("Cloakroom stopped this read: {name} holds {kinds}, which can't be passed on as it is. Don't try other "
             "ways to read it; ask the user.")
GATE_DENY = ("Cloakroom: this command didn't come through Cloakroom's check, so it was stopped. Ask Cursor to run it "
             "again.")
NO_PROGRAM = "Cloakroom can't find its own program, so this command was stopped. Run: cloakroom doctor"
EXPIRED = ("Cloakroom: this command's check expired or was already used, so it wasn't run. Ask Cursor to run it "
           "again.")
WITHHELD = "[Cloakroom couldn't check this output, so it was withheld.]"
CUT = "[Cloakroom: output past 4 MB withheld]"
_RUN_ID = re.compile(r"cursor-run ([A-Za-z0-9_-]{16})")
_IMAGES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")


def _safe(session: str) -> str:
    return "".join(c for c in str(session) if c.isalnum() or c in "-_")[:80] or "default"


def _dir(*parts: str) -> Path:
    d = config.private_dir("cursor")
    for p in parts:
        d = d / p
        d.mkdir(mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
    return d


def _write(path: Path, data: bytes):
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _marker(path: str, session: str) -> Path | None:
    """The empty file that says this version of the file was checked and holds nothing to mask."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = hashlib.sha256(f"{os.path.realpath(path)}:{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:16]
    return config.private_dir("cursor") / _safe(session) / "clean" / key


def _in_copies(path: str) -> bool:
    root = os.path.realpath(config.private_dir("cursor"))
    return os.path.realpath(path).startswith(root + os.sep)


# --- masked read -----------------------------------------------------------------------------------------------

def _ignored(path: str, roots: list) -> bool:
    """Cursor keeps git-ignored and .cursorignore'd files from the agent; a copy outside the workspace would get
    around that, so those files get no copy (read_gate still checks the original read)."""
    try:
        if subprocess.run(["git", "-C", os.path.dirname(path), "check-ignore", "-q", "--", path],
                          capture_output=True, timeout=2).returncode == 0:
            return True
    except Exception:
        pass
    root = str(roots[0]) if roots else ""
    rel = os.path.relpath(path, root) if root else ""
    if not root or rel.startswith(".."):
        return False
    try:
        lines = Path(root, ".cursorignore").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in (ln.strip() for ln in lines):
        if not line or line.startswith(("#", "!")):  # shortcut: no negation or nested .cursorignore files; use a gitignore matcher if users report misses
            continue
        pat = line.lstrip("/")
        if fnmatch(rel, pat) or fnmatch(os.path.basename(rel), pat) or (
                pat.endswith("/") and rel.startswith(pat)):
            return True
    return False


def _kind(head: bytes) -> str:
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(_IMAGES) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return "image"
    return "binary" if b"\0" in head else "text"


def masked_read(data: dict) -> dict | None:
    """preToolUse Read: point Cursor at a masked copy of the file; None leaves the read as it is."""
    args = data.get("tool_input") or {}
    cwd, session = data.get("cwd") or os.getcwd(), data.get("session_id", "default")
    path = args.get("file_path")
    if not isinstance(path, str) or not path:
        return None
    p = os.path.abspath(os.path.join(cwd, os.path.expanduser(path)))
    if not os.path.isfile(p) or _in_copies(p) or _ignored(p, data.get("workspace_roots") or []):
        return None
    name = os.path.basename(p)
    with open(p, "rb") as f:
        head = f.read(8192)
    kind = _kind(head)
    if kind != "pdf" and os.path.getsize(p) > READ_MAX:
        return hook._deny(READ_BIG.format(name=name))
    if kind == "text":
        with open(p, encoding="utf-8", errors="replace") as f:
            resp = media.text_result(p, f.read())
    elif kind == "pdf":
        resp = {"type": "pdf", "file": {"filePath": p}}
    elif kind == "image":
        import base64
        with open(p, "rb") as f:
            resp = {"type": "image", "file": {"filePath": p, "base64": base64.b64encode(f.read()).decode()}}
    else:
        resp = {"type": "binary", "file": {"filePath": p}}
    post = {k: v for k, v in data.items() if k != "tool_use_id"}
    out = hook.on_post({**post, "tool_name": "Read", "tool_input": {"file_path": p}, "tool_response": resp})
    h = (out or {}).get("hookSpecificOutput") or {}
    new = h.get("updatedToolOutput")
    content = (new.get("file") or {}).get("content") if isinstance(new, dict) else None
    if not isinstance(content, str):  # clean (or a picture you let through): the original is read unchanged
        marker = _marker(p, session)
        if marker and not hook._FOUND and not out:
            marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            marker.touch(mode=0o600)
        return None
    copy = _dir(_safe(session)) / f"{hashlib.sha256(p.encode()).hexdigest()[:12]}-{name}{'' if kind == 'text' else '.txt'}"
    _write(copy, content.encode("utf-8"))
    hook._ACTION[0] = hook._ACTION[0] or "masked"
    res = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {**args, "file_path": str(copy)}}}
    if out.get("systemMessage"):
        res["systemMessage"] = out["systemMessage"]
    return res


def read_gate(data: dict) -> dict | None:
    """beforeReadFile: a second line of defence for reads that skipped the redirect. Denies a file whose content
    holds something to mask."""
    path = str(data.get("file_path") or (data.get("tool_input") or {}).get("file_path") or "")
    content, session = data.get("content"), data.get("session_id", "default")
    if not path or not isinstance(content, str) or _in_copies(path):
        return None
    marker = _marker(path, session)
    if marker and marker.exists():
        return None
    cwd = data.get("cwd") or os.getcwd()
    r = client.mask(content, session, roots=[cwd], read=path)
    if not r["masked"]:
        return None
    hook._FOUND.extend((p, k) for p, k, _ in r["masked"])
    kinds = hook._summary([k for _, k, _ in r["masked"]])
    if hook._dry("pre", f"beforeReadFile: would deny ({kinds})"):
        return None
    return hook._deny(READ_DENY.format(name=os.path.basename(path), kinds=kinds))


# --- masked command output -------------------------------------------------------------------------------------

def _runs() -> Path:
    return config.private_dir("cursor-run")


def wrap(data: dict, out: dict | None) -> dict | None:
    """preToolUse Shell, after hook.on_pre: the command is rewritten to run inside `cloakroom cursor-run`. A deny or
    an ask from on_pre is returned as it is."""
    h = (out or {}).get("hookSpecificOutput") or {}
    if h.get("permissionDecision") in ("deny", "ask"):
        return out
    args = data.get("tool_input") or {}
    program = agents._program()
    if not program:
        return hook._deny(NO_PROGRAM)
    shown, ident = str(args.get("command", "")), secrets.token_urlsafe(12)
    wrapper = f"{shlex.quote(program)} cursor-run {ident} {shlex.quote(shown)}"
    # no real values: the runner restores placeholders from the session's encrypted vault when it runs
    record = {"session": data.get("session_id", "default"), "cwd": data.get("cwd") or os.getcwd(), "shown": shown,
              "restore": "updatedInput" in h, "created": time.time(), "wrapper": wrapper}
    _write(_runs() / f"{ident}.json", json.dumps(record).encode())
    res = dict(out or {})
    res["hookSpecificOutput"] = {**h, "hookEventName": "PreToolUse", "updatedInput": {**args, "command": wrapper}}
    return res


def _record(ident: str) -> dict | None:
    try:
        rec = json.loads((_runs() / f"{ident}.json").read_text())
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and time.time() - rec.get("created", 0) <= RUN_TTL else None


def gate(data: dict) -> dict | None:
    """beforeShellExecution: only a command that came through preToolUse (its record is there, fresh, and the command
    is exactly the wrapper) runs. None allows."""
    command = str(data.get("command", ""))
    m = _RUN_ID.search(command)
    rec = _record(m.group(1)) if m else None
    if rec and rec.get("wrapper") == command:
        return None
    return hook._deny(GATE_DENY)


class _Stream:
    """One pipe of the child: a reader thread fills the buffer, take() hands out the part that is safe to mask."""

    def __init__(self, pipe):
        self.fd, self.buf, self.lock = pipe.fileno(), bytearray(), threading.Lock()
        self.last, self.eof, self.total, self.cut, self.noted = time.monotonic(), False, 0, False, False
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while True:
                chunk = os.read(self.fd, 65536)
                if not chunk:
                    break
                with self.lock:
                    room = STREAM_MAX - self.total
                    self.total += len(chunk)
                    self.cut = self.cut or self.total > STREAM_MAX
                    self.buf += chunk[:max(room, 0)]
                    self.last = time.monotonic()
        except OSError:
            pass
        self.eof = True

    def take(self, final: bool) -> bytes:
        with self.lock:
            buf = self.buf
            if not buf:
                return b""
            if not (final or len(buf) >= FLUSH_BYTES or time.monotonic() - self.last >= FLUSH_IDLE):
                return b""
            if final:
                part = bytes(buf)
            else:
                part = bytes(buf[:buf.rfind(b"\n") + 1])
                begin = part.rfind(b"-----BEGIN ")
                if begin != -1 and part.find(b"-----END ", begin) == -1 and len(buf) <= PEM_MAX:
                    part = part[:part.rfind(b"\n", 0, begin) + 1]  # a key block is masked whole
            del buf[:len(part)]
            return part

    def note(self) -> str:
        with self.lock:
            if self.cut and not self.buf and not self.noted:
                self.noted = True
                return CUT + "\n"
        return ""


def _mask(part: bytes, record: dict) -> str:
    """One part of the child's output as hook.on_post masks it. Raw bytes are never returned on a failure."""
    try:
        text = part.decode("utf-8", errors="replace")
        out = hook.handle("post", {
            "session_id": record["session"], "cwd": record["cwd"], "tool_name": "Bash",
            "tool_input": {"command": record["shown"]}, "tool_response": {"stdout": text, "stderr": ""},
            "hook_event_name": "PostToolUse", "_cloakroom_agent": "cursor"})
        new = ((out or {}).get("hookSpecificOutput") or {}).get("updatedToolOutput")
        return new["stdout"] if isinstance(new, dict) and isinstance(new.get("stdout"), str) else text
    except Exception:
        return WITHHELD + "\n"


def _emit(stream, text: str):
    if text:
        stream.write(text)
        stream.flush()


def run(ident: str) -> int:
    """cloakroom cursor-run ID: run the command the record holds once, print its output masked, exit with its code."""
    for f in _runs().glob("*.json"):  # records past their time go with this call
        try:
            if time.time() - json.loads(f.read_text()).get("created", 0) > RUN_TTL:
                f.unlink()
        except (OSError, ValueError, AttributeError):
            pass
    record = _record(ident) if re.fullmatch(r"[A-Za-z0-9_-]{16}", ident or "") else None
    if not record:
        print(EXPIRED, file=sys.stderr)
        return 126
    (_runs() / f"{ident}.json").unlink(missing_ok=True)  # it runs once
    try:
        cmd = record["shown"]
        if record["restore"]:
            cmd = shell.restore(cmd, Vault(config.vault_path(record["session"])).lookup)[0]
    except Exception:
        print(agents.FAILED, file=sys.stderr)
        return 126
    got: list[int] = []
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, lambda n, _f: got.append(n))
    proc = subprocess.Popen([os.environ.get("SHELL") or "/bin/sh", "-c", cmd], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)
    streams = [(_Stream(proc.stdout), sys.stdout), (_Stream(proc.stderr), sys.stderr)]
    sent = exited = None
    while True:
        time.sleep(0.2)  # shortcut: polling, not select; the child's output waits at most 0.2 s more
        if got and sent is None:
            sent = time.monotonic()
            _signal_group(proc, got[0])
        elif sent is not None and proc.poll() is None and time.monotonic() - sent > 5:
            _signal_group(proc, signal.SIGKILL)
        if proc.poll() is not None and exited is None:
            exited = time.monotonic()
        # the last part of a stream is flushed once both pipes are read to their end (or a background child holds one)
        final = exited is not None and (all(s.eof for s, _ in streams) or time.monotonic() - exited > GRACE)
        for stream, out in streams:
            part = stream.take(final)
            _emit(out, _mask(part, record) if part else "")
            _emit(out, stream.note())
        if final:
            break
    if got:
        return 128 + got[0]
    return proc.returncode if proc.returncode >= 0 else 128 - proc.returncode


def _signal_group(proc, sig):
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


# --- cleanup ---------------------------------------------------------------------------------------------------

def cleanup(session: str):
    """sessionEnd: the session's copies and its run records."""
    shutil.rmtree(config.private_dir("cursor") / _safe(session), ignore_errors=True)
    for f in _runs().glob("*.json"):
        try:
            if json.loads(f.read_text()).get("session") == session:
                f.unlink()
        except (OSError, ValueError, AttributeError):
            pass


def sweep():
    """sessionStart: copies of sessions that never ended (a crash, say), untouched for more than a day."""
    for d in config.private_dir("cursor").iterdir():
        try:
            if time.time() - d.stat().st_mtime > SWEEP_AFTER:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
