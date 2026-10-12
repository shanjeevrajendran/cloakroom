"""Client for the local service, used by hooks. In rules mode it can run in-process without the service."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

from . import config, engine
from .vault import Vault


class Unavailable(RuntimeError):
    """The service isn't reachable (it is being started in the background) - callers must fail closed."""


STARTUP_WAIT = float(os.environ.get("CLOAKROOM_STARTUP_WAIT", "40"))  # hooks have 60 s; model load takes 4-25 s


def _wait_until_up(port: int, seconds: float) -> bool:
    """After starting the service, wait for it to answer /health as Cloakroom (the first check of a session would
    otherwise be denied while the model loads)."""
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        h = health(port)
        if h is not None:
            return h.get("service") == "cloakroom"
        time.sleep(0.5)
    return False


class _Down(Exception):
    """Nothing is listening yet."""


def _once(req, timeout: float, port: int) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e)
        except Exception:
            detail = {}
        if not is_cloakroom(port):
            raise Unavailable(port_taken(port)) from e
        raise Unavailable(detail.get("detail") or detail.get("error") or f"HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise _Down() from e


def _post(path: str, body: dict, timeout: float = 30, wait: float | None = None) -> dict:
    cfg = config.load()
    req = urllib.request.Request(f"http://127.0.0.1:{cfg['port']}{path}", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "X-Cloakroom-Token": config.token()})
    try:
        return _once(req, timeout, cfg["port"])
    except _Down:
        start_background()
    if not _wait_until_up(cfg["port"], STARTUP_WAIT if wait is None else wait):
        raise Unavailable("cloakroom service is starting; try again in a few seconds")
    try:
        return _once(req, timeout, cfg["port"])
    except _Down as e:
        raise Unavailable("cloakroom service stopped answering; run: cloakroom doctor") from e


def health(port: int) -> dict | None:
    """The service's /health, or None if nothing answers. Another program on the port answers too: check 'service'."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            h = json.load(r)
        return h if isinstance(h, dict) else {}
    except Exception:
        return None


def is_cloakroom(port: int) -> bool:
    h = health(port)
    return h is None or h.get("service") == "cloakroom"


def port_taken(port: int) -> str:
    return (f"port {port} is used by another program, not Cloakroom. Pick a free port: set \"port\" in "
            f"~/.cloakroom/config.json (for example {{\"port\": 37788}}) or CLOAKROOM_PORT, then restart Claude Code")


def start_background():
    """Start `cloakroom serve` detached, once. Output goes to ~/.cloakroom/serve.log (never values)."""
    log = config.private_dir() / "serve.log"
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    if os.name == "nt":  # detach from the hook's console so the service outlives it, without a window
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        detach = {"creationflags": flags}
    else:
        detach = {"start_new_session": True}
    with os.fdopen(fd, "ab") as out:
        subprocess.Popen([sys.executable, "-m", "cloakroom", "serve"], stdout=out, stderr=out,
                         stdin=subprocess.DEVNULL, env=os.environ.copy(), **detach)


def mask(text: str, session: str) -> dict:
    cfg = config.load()
    if cfg["mode"] == "rules":
        r = engine.mask(text, Vault(config.vault_path(session)), "rules", cfg["allow"], cfg["always"])
        return {"text": r.text, "mode": "rules", "masked": r.masked, "skipped": r.skipped}
    return _post("/mask", {"text": text, "session": session})


def restore(text: str, session: str) -> dict:
    if config.load()["mode"] == "rules":
        out, unknown = Vault(config.vault_path(session)).restore(text)
        return {"text": out, "unknown": unknown}
    return _post("/restore", {"text": text, "session": session})
