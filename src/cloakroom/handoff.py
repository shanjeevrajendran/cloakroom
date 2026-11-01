"""Safe handoff: `cloakroom give NAME` stores a value you type hidden in a terminal and prints a placeholder to paste.

The value never goes through a prompt. It is kept encrypted (crypto.py) per project under the Cloakroom home; every
session in that project (Claude Code, Codex CLI, Gemini CLI, Cursor) loads it into its vault
when the hook starts (vault.py load_given), so the existing restore puts the real value back into local files and commands, and tool output that
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
import threading
import time
from pathlib import Path

from . import config, crypto
from .vault import TAG_INFO, TAG_LEN, Vault

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


# --- the proxy's store (proxy.py): the same file format and expiry as `give`, under one name no folder can have ---
# A value the proxy masks in a request gets a placeholder here. Every session's vault loads these next to the
# project's handed-over values (vault.py load_given), so a hook restores the placeholder when the model writes it
# into a local file or command, whichever project or agent that is.

PROXY = "\0proxy"
_MINT = threading.Lock()  # the service and the proxy mint from threads
PROXY_SESSION = "(proxy)"  # not a name vault_path() can make, so no hook session is mistaken for it


def proxy_values() -> dict[str, str]:
    """{placeholder: value} the proxy has masked in the last 24 hours. {} when none, or the store can't be read
    (then nothing is restored, which leaves the placeholder as text: the safe side)."""
    try:
        return {e["placeholder"]: e["value"] for e in entries(PROXY).values()} if _path(PROXY).exists() else {}
    except (OSError, ValueError):
        return {}


def proxy_placeholder(value: str, kind: str, hours: float = HOURS) -> str:
    """The placeholder for value, minted on first sight and kept 24 hours from the last time it was masked."""
    with _MINT:
        return _mint(value, kind, hours)


def _mint(value: str, kind: str, hours: float) -> str:
    store = _load(PROXY)
    store["entries"] = _live(store)
    name = hashlib.sha256(f"{kind}|{value}".encode()).hexdigest()[:24]
    old = store["entries"].get(name)
    if old and old["expires"] > time.time() + 3600:
        return old["placeholder"]  # most of its time is left: no write
    if old:
        base = old["placeholder"].rsplit("_", 1)[0]
    else:
        counts = store.setdefault("counts", {})
        counts[kind] = counts.get(kind, 0) + 1
        base = f"PII_{kind}_{counts[kind]}"
    mac = hmac.new(hmac.new(crypto.key(), TAG_INFO, hashlib.sha256).digest(),
                   f"proxy|{store['id']}|{base}".encode(), hashlib.sha256).digest()
    ph = f"{base}_{base64.b32encode(mac).decode().lower()[:TAG_LEN]}"
    store["entries"][name] = {"placeholder": ph, "value": value, "expires": time.time() + hours * 3600}
    _save(PROXY, store)
    return ph


class ProxyVault(Vault):
    """What engine.mask writes placeholders into for the proxy: the store above, not a session file."""

    SESSION = PROXY_SESSION

    def __init__(self):
        super().__init__(None)
        self.session = self.SESSION
        self._lock = threading.Lock()
        self.load_given()

    def load_given(self):
        with self._lock:
            self.by_placeholder = proxy_values()
            self.by_value = {v: ph for ph, v in self.by_placeholder.items()}

    def placeholder(self, value: str, kind: str, derived: bool = False) -> str:
        with self._lock:
            ph = proxy_placeholder(value, kind)
            self.by_value[value], self.by_placeholder[ph] = ph, value
            return ph

    def lookup(self, placeholder: str) -> str | None:
        return self.by_placeholder.get(placeholder)


def vault_for(session: str) -> Vault:
    """The vault for a hook session, or the proxy's store for the proxy's own session name."""
    return ProxyVault() if session == PROXY_SESSION else Vault(config.vault_path(session))
