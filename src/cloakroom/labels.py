"""The user's verdicts on events (false alarm / correct / missed), kept AES-GCM encrypted in ~/.cloakroom/labels.bin.

Encrypted because a label may carry the value it is about (a false alarm on "France", a missed ID), which the
learning step needs to propose a rule. One label per event; labelling again replaces it.
"""
from __future__ import annotations

import json
import os
import time

from . import config, crypto

KINDS = ("false_alarm", "correct", "missed")


def _path():
    return config.private_dir() / "labels.bin"


def load() -> dict[str, dict]:
    path = _path()
    return json.loads(crypto.decrypt(path.read_bytes())) if path.exists() else {}


def _save(labels: dict[str, dict]):
    path = _path()
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(crypto.encrypt(json.dumps(labels).encode()))
    os.replace(tmp, path)


def put(event_id: str, label: str, items: list[dict] | None = None) -> dict:
    """items: the values the label is about, [{"kind", "value"}] (the safe ones of a false alarm, what was missed)."""
    if label not in KINDS:
        raise ValueError(f"label must be one of {', '.join(KINDS)}")
    labels = load()
    items = [{"kind": str(i.get("kind", "")), "value": str(i.get("value", ""))} for i in items or []]
    labels[event_id] = {"label": label, "items": items, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _save(labels)
    return labels[event_id]


def remove(event_id: str) -> bool:
    labels = load()
    if labels.pop(event_id, None) is None:
        return False
    _save(labels)
    return True
