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


def _post(path: str, body: dict, timeout: float = 30) -> dict:
    cfg = config.load()
    req = urllib.request.Request(f"http://127.0.0.1:{cfg['port']}{path}", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "X-Cloakroom-Token": config.token()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = json.load(e) if e.headers.get("Content-Type") == "application/json" else {}
        raise Unavailable(detail.get("detail") or detail.get("error") or f"HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        start_background()
        raise Unavailable("cloakroom service is starting; try again in a few seconds") from e


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
        r = engine.mask(text, Vault(config.vault_path(session)), "rules", cfg["allow"])
        return {"text": r.text, "mode": "rules", "masked": r.masked, "skipped": r.skipped}
    return _post("/mask", {"text": text, "session": session})


def restore(text: str, session: str) -> dict:
    if config.load()["mode"] == "rules":
        out, unknown = Vault(config.vault_path(session)).restore(text)
        return {"text": out, "unknown": unknown}
    return _post("/restore", {"text": text, "session": session})
