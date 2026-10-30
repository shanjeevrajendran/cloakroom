"""Safe handoff: `cloakroom give NAME` stores a value you type hidden in a terminal and prints a placeholder to paste.

The value never goes through a prompt. It is kept encrypted (crypto.py) per project under the Cloakroom home; every
session in that project (Claude Code, Codex CLI, Gemini CLI) loads it into its vault when the hook starts (vault.py
load_given), so the existing restore puts the real value back into local files and commands, and tool output that
shows it is masked again. Placeholders are the usual signed ones (kind SECRET), tagged with a key only this machine
has. Entries expire after 24 hours. Values are never printed, logged or shown on the dashboard; the list shows a hint.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path

from . import config, crypto
from .vault import TAG_INFO, TAG_LEN

KIND = "SECRET"
HOURS = 24
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,39}")


def project_of(cwd: str | os.PathLike) -> str:
    """The folder holding .git above cwd, else cwd itself."""
    here = Path(cwd).resolve()
    return str(next((p for p in (here, *here.parents) if (p / ".git").exists()), here))


def _path(project: str) -> Path:
    return config.private_dir("handoff") / (hashlib.sha256(project.encode()).hexdigest()[:16] + ".bin")


def _load(project: str) -> dict:
    path = _path(project)
    if path.exists():
        return json.loads(crypto.decrypt(path.read_bytes()))
    return {"id": secrets.token_hex(16), "n": 0, "entries": {}}


def _save(project: str, store: dict):
    path = _path(project)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(crypto.encrypt(json.dumps(store).encode()))
    os.replace(tmp, path)


def _live(store: dict) -> dict:
    now = time.time()
    return {n: e for n, e in store["entries"].items() if e["expires"] > now}


def hint(value: str) -> str:
    """Enough to recognise the key, not enough to guess it; short values show only their length."""
    return f"{value[:3]}…{value[-3:]}" if len(value) >= 12 else f"({len(value)} characters)"


def give(name: str, value: str, project: str, hours: float = HOURS) -> str:
    """Store value under name for this project and return its placeholder. Giving a name again replaces the value
    and keeps the placeholder, so one the agent already holds stays good."""
    if not NAME.fullmatch(name):
        raise ValueError("NAME is a letter, then letters, digits, _ . - (40 at most)")
    if not value:
        raise ValueError("no value entered")
    store = _load(project)
    store["entries"] = _live(store)
    old = store["entries"].get(name)
    if old:
        base = old["placeholder"].rsplit("_", 1)[0]
    else:
        store["n"] += 1
        base = f"PII_{KIND}_{store['n']}"
    mac = hmac.new(hmac.new(crypto.key(), TAG_INFO, hashlib.sha256).digest(),
                   f"handoff|{store['id']}|{project}|{base}".encode(), hashlib.sha256).digest()
    ph = f"{base}_{base64.b32encode(mac).decode().lower()[:TAG_LEN]}"
    store["entries"][name] = {"placeholder": ph, "value": value, "hint": hint(value), "expires": time.time() + hours * 3600}
    _save(project, store)
    return ph


def entries(project: str) -> dict:
    return _live(_load(project))


def forget(name: str, project: str) -> bool:
    store = _load(project)
    gone = store["entries"].pop(name, None) is not None
    store["entries"] = _live(store)
    _save(project, store)
    return gone


def _at(session: str) -> Path:
    return config.private_dir("handoff/at") / config.vault_path(session).stem


def note_session(session: str, cwd: str):
    """Remember which project a session works in, so its vault finds the project's handed-over values."""
    project, at = project_of(cwd), _at(session)
    if at.exists() and at.read_text() == project:
        return
    fd = os.open(at, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(project)


def values(session: str) -> dict[str, str]:
    """{placeholder: value} handed over for this session's project, expired ones left out."""
    try:
        at = (config.HOME / "handoff" / "at" / config.vault_path(session).stem)
        project = at.read_text()
    except OSError:
        return {}
    if not _path(project).exists():
        return {}
    return {e["placeholder"]: e["value"] for e in entries(project).values()}
