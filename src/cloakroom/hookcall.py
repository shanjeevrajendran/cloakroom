"""What one hook call notes down, and holding a call until you allow it.

The notes are per call: hook.handle() empties them when a call starts and writes them to the log when it ends. A
hook is one process per call, so they are plain module values; cursor.py and githook.py, which run a check outside
a hook call, write them too.

A held call is denied and remembered as a keyed hash of its command (or of the tool name and its input), so the
file never holds the command or a secret in it. Typing cr: allow arms it: the same call then passes once.
"""
from __future__ import annotations

import json
import os
import time

from . import config

_ACTION = [""]  # set when a prompt passes by your own choice (sent anyway, paused), for the event log
_NOTE = [""]  # extra detail for the hook.log line (OCR pages and time), never values
_FOUND: list[tuple[str, str]] = []  # (placeholder, kind) found during the current hook call, for the event log
_EXTRA: dict = {}  # extra event-log fields for this hook call (session start: version, mode, hooks)
_SKIPS: list[tuple] = []  # checks this hook call let through without running in full: (reason, check, what, say, extra)


def _skip(reason: str, check: str, what: str = "", say: bool = True, ms: float = 0.0, **numbers):
    """A check that let this call through without running in full (fail-open): logged as a "skipped" event (no
    values) and, when say, told to you once per session for each reason and check. what: the check in the message."""
    _SKIPS.append((reason, check, what or f"this {check}", say, {"ms": round(ms, 1), **numbers}))


def _summary(kinds: list[str]) -> str:
    counts = {}
    for k in kinds:
        counts[k] = counts.get(k, 0) + 1
    return ", ".join(f"{k} x{n}" for k, n in sorted(counts.items()))


def _dry(event: str, note: str) -> bool:
    if not os.environ.get("CLOAKROOM_DRY_RUN"):
        return False
    _ACTION[0] = "dry-run"
    with open(config.private_dir() / "dry-run.log", "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {event} {note}\n")
    return True


HOLD_MINUTES = 10  # how long a held command can be allowed with cr: allow


def _held_file():
    return config.private_dir() / "held.json"


def _command_mac(command: str) -> str:
    """A keyed hash of a held command, so the file never holds the command (or the secret in it)."""
    import hashlib
    import hmac
    return hmac.new(config.token().encode(), command.encode(), hashlib.sha256).hexdigest()


def _write_held(state: dict):
    fd = os.open(_held_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f)


def _read_held() -> dict | None:
    try:
        state = json.loads(_held_file().read_text())
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and state.get("until", 0) > time.time() else None


def _arm_held() -> str:
    """cr: allow - let the last held command run once (Claude can't type this: it is your prompt)."""
    state = _read_held()
    if not state:
        return "Cloakroom: no command is being held right now, so there is nothing to allow."
    state["armed"] = True
    _write_held(state)
    return ("Cloakroom: the held command may run once. Ask Claude to run it again, unchanged; a changed command is "
            "held again.")


def _allowed_once(command: str) -> bool:
    """The command you allowed with cr: allow, run unchanged: it passes this one time."""
    state = _read_held()
    if not (state and state.get("armed") and state.get("mac") == _command_mac(command)):
        return False
    _held_file().unlink(missing_ok=True)
    return True


def _hold(key: str, reason: str, dry: str = "") -> dict | None:
    """Hold a call until you type cr: allow: the deny answer with reason, and the call is noted (as a keyed hash of
    key: the command, or the tool name and its input) so that only the same call passes, once, within HOLD_MINUTES.
    None when this is that call, or in a dry run (dry: its log line; "" for a hold that a dry run keeps)."""
    if _allowed_once(key):
        _ACTION[0] = "allowed once"
        return None
    if dry and _dry("pre", dry):
        return None
    _write_held({"mac": _command_mac(key), "until": time.time() + 60 * HOLD_MINUTES, "armed": False})
    _ACTION[0] = "held"
    return _deny(reason)


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


# What other modules use from here (cursor.py, githook.py, gitcheck.py). hook.py takes them under the names it
# always had. _arm_held and _write_held have no public name on purpose: a command that imports them is refused
# (hook._SELF_CLI), and that check goes by their names.
ACTION, NOTE, FOUND, EXTRA, SKIPS = _ACTION, _NOTE, _FOUND, _EXTRA, _SKIPS
skip, summary, dry, deny, hold = _skip, _summary, _dry, _deny, _hold
