"""Your own rules, learned from your labels: ~/.cloakroom/rules.bin, AES-GCM encrypted like the vault.

  allow        never mask this value (from a false alarm)
  always_mask  always mask this value or pattern (from a miss)
Text is an exact value, or 're:<regex>' matched against the whole value (allow) or anywhere in the text
(always_mask). A rule is 'suggested' until you approve it on the dashboard; only 'active' rules change masking.
Encrypted because an always-mask rule is the very value you didn't want the model to see. Hand-written allow
entries still go in config.json's "allow" list.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time

from . import config, crypto

TYPES = ("allow", "always_mask")
SECRET_KINDS = {"SECRET", "CARD", "SSN", "IBAN", "ACCOUNT", "PASSWORD"}  # never applied without your approval


def _path():
    return config.HOME / "rules.bin"


def load() -> list[dict]:
    path = _path()
    return json.loads(crypto.decrypt(path.read_bytes()))["rules"] if path.exists() else []


def _save(rules: list[dict]):
    path = config.private_dir() / "rules.bin"
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(crypto.encrypt(json.dumps({"rules": rules}).encode()))
    os.replace(tmp, path)


def effective() -> tuple[list[str], list[tuple[str, str]]]:
    """Active rules as the engine takes them: (allow entries, [(always-mask entry, kind)]).
    No key, or the wrong one: skip your rules with a warning (masking still runs on the built-in rules)."""
    from cryptography.exceptions import InvalidTag
    try:
        rules = load()
    except (crypto.KeyUnavailable, InvalidTag, ValueError, KeyError) as e:
        why = (str(e) if isinstance(e, crypto.KeyUnavailable) else
               "rules.bin doesn't decrypt with this vault key" if isinstance(e, InvalidTag) else
               "rules.bin is damaged (delete it to start your rules over)")
        print(f"cloakroom: skipping your rules: {why}", file=sys.stderr)
        return [], []
    active = [r for r in rules if r["state"] == "active"]
    return ([r["text"] for r in active if r["type"] == "allow"],
            [(r["text"], re.sub(r"[^A-Z]", "", r["kind"].upper()) or "SECRET")  # placeholders take letters only
             for r in active if r["type"] == "always_mask"])


def pattern_for(value: str) -> str:
    """A miss with digits generalises to its shape (ACCT-482913 -> re:ACCT\\-[0-9]{6}); text stays exact."""
    if not re.search(r"\d", value):
        return value
    return "re:" + re.sub(r"(\d+)|(\D+)", lambda m: f"[0-9]{{{len(m[1])}}}" if m[1] else re.escape(m[2]), value)


def suggest(event_id: str, label: str, items: list[dict], where: str = "") -> list[dict]:
    """Turn one label into suggested rules. Same type + text as an existing rule: no duplicate."""
    if label not in ("false_alarm", "missed"):
        return []
    rules, added = load(), []
    for i in items:
        value, kind = i.get("value", "").strip(), i.get("kind", "")
        if not value:
            continue
        rtype = "allow" if label == "false_alarm" else "always_mask"
        text = value if rtype == "allow" else pattern_for(value)
        if any(r["type"] == rtype and r["text"] == text for r in rules):
            continue
        why = (f"you marked it a false alarm{' in ' + where if where else ''}" if rtype == "allow"
               else f"you flagged a {kind or 'value'} it missed{' in ' + where if where else ''}")
        r = {"id": secrets.token_hex(5), "type": rtype, "text": text, "kind": kind, "state": "suggested",
             "event": event_id, "why": why, "needs_yes": rtype == "allow" and kind in SECRET_KINDS,
             "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "decided": None}
        rules.append(r)
        added.append(r)
    if added:
        _save(rules)
    return added


def drop_suggestions(event_id: str):
    """A label was cleared: its still-pending suggestions go too (approved rules stay until you undo them)."""
    rules = load()
    keep = [r for r in rules if not (r["event"] == event_id and r["state"] == "suggested")]
    if len(keep) != len(rules):
        _save(keep)


def decide(rule_id: str, action: str) -> dict:
    """approve (suggested -> active), dismiss (drop a suggestion), undo (drop an active rule)."""
    rules = load()
    r = next((r for r in rules if r["id"] == rule_id), None)
    if not r:
        raise ValueError("unknown rule")
    if action == "approve" and r["state"] == "suggested":
        r["state"], r["decided"] = "active", time.strftime("%Y-%m-%dT%H:%M:%S")
    elif action in ("dismiss", "undo"):
        rules.remove(r)
        _log_decision(action)
    else:
        raise ValueError(f"can't {action} a rule that is {r['state']}")
    _save(rules)
    return r


def _log_decision(action: str):
    """Dismissed and undone rules leave the file, but still count as decisions you made."""
    path = config.private_dir() / "decisions.json"
    counts = json.loads(path.read_text()) if path.exists() else {}
    counts[action] = counts.get(action, 0) + 1
    path.write_text(json.dumps(counts))


def decisions() -> int:
    path = config.HOME / "decisions.json"
    gone = sum(json.loads(path.read_text()).values()) if path.exists() else 0
    return gone + sum(1 for r in load() if r["decided"])
