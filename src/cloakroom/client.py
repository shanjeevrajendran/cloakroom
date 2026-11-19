"""Client for the local service, used by hooks. In rules mode it can run in-process without the service."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import config, engine, filelog, handoff


class Unavailable(RuntimeError):
    """The service isn't reachable (it is being started in the background) - callers must fail closed."""


STARTUP_WAIT = float(os.environ.get("CLOAKROOM_STARTUP_WAIT", "40"))  # hooks have 60 s; model load takes 4-25 s
# The agents stop waiting for a hook after this many seconds (gen_hooks.py, agents.TIMEOUTS). What happens then is
# theirs to decide, and Gemini goes on with the unchecked original, so a hook answers before that: its calls to the
# service share one clock, and when it runs out the answer is the fail-closed one.
HOOK_SECONDS = {"start": 30, "prompt": 60, "pre": 60, "post": 120, "scrub": 30}
REQUEST_SECONDS = 30.0  # one request when no hook clock runs (the proxy, the command line)
SLOW = "the check took longer than the agent waits for Cloakroom"
_UNTIL = [0.0]  # time.monotonic() when the running hook must have answered; 0: no clock


def start_clock(event: str):
    """A hook call starts: it has its agent's time limit, less a margin to print the answer."""
    _UNTIL[0] = time.monotonic() + HOOK_SECONDS.get(event, 60) * 0.85 - 2


def stop_clock():
    _UNTIL[0] = 0.0


def seconds_left(most: float | None = None) -> float:
    """Seconds one request may take: what is left on the hook's clock (REQUEST_SECONDS without one), at most `most`."""
    left = _UNTIL[0] - time.monotonic() if _UNTIL[0] else REQUEST_SECONDS
    return left if most is None else min(left, most)


def _wait_until_up(port: int, seconds: float) -> bool:
    """After starting the service, wait for it to answer /health as Cloakroom (the first check of a session would
    otherwise be denied while the model loads)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        h = health(port)
        if h is not None:
            return h.get("service") == "cloakroom"
        time.sleep(0.5)
    return False


class _Down(Exception):
    """Nothing is listening yet."""


def _timed_out(e: BaseException) -> bool:
    return isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError)


def _once(req, timeout: float, port: int) -> dict:
    if timeout <= 0:
        raise Unavailable(SLOW)
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
        if _timed_out(e):
            # the service is there and busy (a long check, or still loading its model). Asking again would make
            # it do the work twice, and starting a second service helps nothing: this call fails closed.
            raise Unavailable(SLOW) from e
        raise _Down() from e


def _post(path: str, body: dict, timeout: float | None = None, wait: float | None = None) -> dict:
    """timeout: the most seconds for one request; the hook's clock (start_clock) caps it."""
    cfg = config.load()

    def request():
        left = seconds_left(timeout)
        sent = {**body, "seconds": round(left, 2)} if path == "/mask" else body  # the service stops there too
        return urllib.request.Request(f"http://127.0.0.1:{cfg['port']}{path}", json.dumps(sent).encode(),
                                      {"Content-Type": "application/json", "X-Cloakroom-Token": config.token()}), left
    try:
        return _once(*request(), cfg["port"])
    except _Down:
        start_background()
    wait = STARTUP_WAIT if wait is None else wait
    if _UNTIL[0]:  # a hook's clock runs: not past it
        wait = min(wait, max(0.0, seconds_left()))
    if not _wait_until_up(cfg["port"], wait):
        raise Unavailable("cloakroom service is starting; try again in a few seconds")
    try:
        return _once(*request(), cfg["port"])
    except _Down as e:
        raise Unavailable("cloakroom service stopped answering; run: cloakroom doctor") from e


def dashboard_code() -> str:
    """A one-time sign-in code for the dashboard, from the running service."""
    return _post("/dashboard/code", {})["code"]


def warm_up(session: str):
    """Start the service if it isn't running and return at once: Unavailable says it is still starting."""
    _post("/mask", {"text": "warm up", "session": session}, timeout=5, wait=0)


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


def start_background(command: str = "serve"):
    """Start `cloakroom serve` (or another command) detached, once. Output goes to ~/.cloakroom/serve.log (never values)."""
    log = config.private_dir() / "serve.log"
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    if os.name == "nt":  # detach from the hook's console so the service outlives it, without a window
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        detach = {"creationflags": flags}
    else:
        detach = {"start_new_session": True}
    with os.fdopen(fd, "ab") as out:
        subprocess.Popen([sys.executable, "-m", "cloakroom", *command.split()], stdout=out, stderr=out,
                         stdin=subprocess.DEVNULL, env=os.environ.copy(), **detach)


def mask(text: str, session: str, ocr: bool = False, roots: list[str] | None = None, read: str = "") -> dict:
    """ocr: the text came from OCR (engine.OCR_DIGITS also applies). roots: the working folder(s), and read: the file
    being read, whose file-log entries apply (filelog.py)."""
    cfg = config.load()
    if cfg["mode"] == "rules":
        files = filelog.known(roots, read) if roots else None
        r = engine.mask(text, handoff.vault_for(session), "rules", cfg["allow"], cfg["always"], ocr, files)
        return {"text": r.text, "mode": "rules", "masked": r.masked, "skipped": r.skipped}
    return _post("/mask", {"text": text, "session": session, **({"ocr": True} if ocr else {}),
                           **({"roots": roots, "read": read} if roots else {})})


def restore(text: str, session: str) -> dict:
    if config.load()["mode"] == "rules":
        out, unknown = handoff.vault_for(session).restore(text)
        return {"text": out, "unknown": unknown}
    return _post("/restore", {"text": text, "session": session})
