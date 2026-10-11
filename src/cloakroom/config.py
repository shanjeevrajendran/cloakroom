"""Settings: ~/.cloakroom/config.json, overridden by CLOAKROOM_* environment variables."""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

HOME = Path(os.environ.get("CLOAKROOM_HOME", Path.home() / ".cloakroom"))
DEFAULTS = {"mode": "full", "port": 8787, "allow": []}


def load() -> dict:
    cfg = dict(DEFAULTS)
    path = HOME / "config.json"
    if path.exists():
        cfg.update(json.loads(path.read_text()))
    if os.environ.get("CLOAKROOM_MODE"):
        cfg["mode"] = os.environ["CLOAKROOM_MODE"]
    if os.environ.get("CLOAKROOM_PORT"):
        cfg["port"] = int(os.environ["CLOAKROOM_PORT"])
    if cfg["mode"] not in ("full", "rules"):
        raise ValueError("mode must be 'full' or 'rules'")
    return cfg


def private_dir(name: str = "") -> Path:
    d = HOME / name if name else HOME
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)
    return d


def token() -> str:
    """Shared secret between hooks and the local service, so no other local process can call /restore."""
    path = private_dir() / "token"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32))
    return path.read_text().strip()


def vault_path(session: str) -> Path:
    safe = "".join(c for c in session if c.isalnum() or c in "-_")[:80] or "default"
    return private_dir("sessions") / f"{safe}.json"
