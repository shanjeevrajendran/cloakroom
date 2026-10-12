"""Local dashboard, served by the service at http://127.0.0.1:<port>/dashboard. Never reachable off this machine.

Sign-in: `cloakroom dashboard` asks the service (with the token) for a one-time code that works once, for 60 s, and
opens /dashboard?code=...; the service swaps it for an HttpOnly, SameSite=Strict cookie and drops the code from the
address bar. Writes also need the X-Cloakroom header, which a page on another site can't send without a preflight
the service never answers. Real values only reach the page when you click Reveal, one event at a time.
"""
from __future__ import annotations

import hmac
import secrets
import time
from http.cookies import SimpleCookie
from pathlib import Path

from . import __version__, config, engine, events, labels, learn, userrules
from .vault import Vault

COOKIE = "cloakroom_dash"
CODE_SECONDS = 60
_CODES: dict[str, float] = {}
_SESSION = secrets.token_urlsafe(32)  # one per service start; restarting signs the browser out
HTML = Path(__file__).with_name("dashboard.html")


def new_code() -> str:
    now = time.monotonic()
    for c in [c for c, exp in _CODES.items() if exp < now]:
        del _CODES[c]
    code = secrets.token_urlsafe(18)
    _CODES[code] = now + CODE_SECONDS
    return code


def redeem(code: str) -> bool:
    exp = _CODES.pop(code, None)
    return exp is not None and exp >= time.monotonic()


def set_cookie() -> str:
    return f"{COOKIE}={_SESSION}; HttpOnly; SameSite=Strict; Path=/"


def signed_in(cookie_header: str | None) -> bool:
    try:
        c = SimpleCookie(cookie_header or "")
    except Exception:
        return False
    return COOKIE in c and hmac.compare_digest(c[COOKIE].value, _SESSION)


def page() -> bytes:
    return HTML.read_bytes()


def summary(limit: int = 2000) -> dict:
    cfg = config.load()
    found = labels.load()
    evs = events.read(limit)
    for e in evs:
        if e["id"] in found:
            e["label"] = found[e["id"]]["label"]
    sessions = config.HOME / "sessions"
    return {"version": __version__, "mode": cfg["mode"], "installing": cfg.get("installing"), "port": cfg["port"],
            "model": engine._MODEL is not None, "allow": len(cfg.get("allow", [])),
            "vault_sessions": len(list(sessions.glob("*.json"))) if sessions.exists() else 0,
            "decisions": len(found) + userrules.decisions(), "rules": [_public(r) for r in userrules.load()],
            "learning": learn.mode(), "unlock": learn.UNLOCK,
            "events": evs}


def _public(rule: dict) -> dict:
    """An allow rule on a name shows its text (you judged it safe); a secret-type value or an always-mask value shows
    only a hint, so the page never carries it."""
    r = {k: rule[k] for k in ("id", "type", "kind", "state", "why", "needs_yes", "event")}
    r["auto"], r["replay"] = bool(rule.get("auto")), rule.get("replay", "")
    t = rule["text"]
    plain = t.startswith("re:") or (rule["type"] == "allow" and rule["kind"] not in userrules.SECRET_KINDS)
    r["text"] = t if plain else t[:2] + "•" * min(8, max(3, len(t) - 2))
    return r


def rule(body: dict) -> dict:
    """{"id", "action": approve|dismiss|undo}"""
    return {"ok": True, "rule": _public(userrules.decide(str(body.get("id", "")), str(body.get("action", ""))))}


def _event(event_id: str) -> dict | None:
    return next((e for e in events.read(10_000) if e["id"] == event_id), None)


def values(event_id: str) -> list[dict]:
    """The real values behind one event's placeholders, from its session vault. Only on an explicit Reveal."""
    ev = _event(event_id)
    if not ev:
        return []
    vault = Vault(config.vault_path(ev["session"]))
    return [{"placeholder": p, "kind": p.split("_")[1], "value": vault.by_placeholder.get(p, "")}
            for p in ev.get("placeholders", [])]


def label(body: dict) -> dict:
    """{"id", "label": false_alarm|correct|missed|none, "placeholders"?: [...], "missed"?: [{"kind", "value"}]}.
    For a false alarm the browser names placeholders and the values are looked up here, so they needn't round-trip."""
    event_id, lab = str(body.get("id", "")), body.get("label")
    if not _event(event_id):
        raise ValueError("unknown event")
    if lab == "none":
        labels.remove(event_id)
        userrules.drop_suggestions(event_id)
        return {"ok": True}
    items = []
    if lab == "false_alarm":
        chosen = set(body.get("placeholders") or [])
        items = [{"kind": v["kind"], "value": v["value"]} for v in values(event_id) if v["placeholder"] in chosen]
    elif lab == "missed":
        items = [i for i in body.get("missed") or [] if str(i.get("value", "")).strip()]
        if not items:
            raise ValueError("say what was missed")
    labels.put(event_id, lab, items)
    userrules.drop_suggestions(event_id)  # relabelled: the old label's pending suggestions no longer apply
    added = userrules.suggest(event_id, lab, items, _event(event_id).get("target", ""))
    changed = learn.after_label(added, values)
    applied = sum(1 for r in changed if r.get("auto") and r["state"] == "active")
    return {"ok": True, "suggested": len(added) - applied, "applied": applied,
            "paused": sum(1 for r in changed if r["state"] == "suggested")}


def set_learning(body: dict) -> dict:
    found = labels.load()
    return {"ok": True, "mode": learn.set_mode(str(body.get("mode", "")), len(found) + userrules.decisions())}
