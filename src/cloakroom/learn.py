"""Learn quietly: rules from your labels apply on their own, but only after a replay test against your own decisions
and a built-in safety set. Suggest mode infers the same rules and waits for your Approve.

Replay test, per rule (values are compared, never logged):
  allow        must not unmask anything you labelled Correct or Missed, nor any sample secret below; and it never
               applies on its own for keys, cards, account and ID numbers (those always wait for you)
  always_mask  must not re-mask anything you called a false alarm, nor ordinary text below (too broad a pattern)
Unlocks after UNLOCK decisions. Every new label re-checks the rules that applied on their own; one that now conflicts
goes back to suggested, with the reason.
"""
from __future__ import annotations

import json
import re

from . import config, labels, userrules

UNLOCK = 10
# synthetic keys, split so secret scanners don't flag the source file
SAFETY_SECRETS = ["sk_" + "live_51Hx9QaZb8Lc2Lc93Kq2LcMn", "AKIA" + "QX4TZ7LMNP2RW8KD", "ghp_" + "7hQ2xLmN4pR8sT1vW3yZ5aB6cD9eF0gH2jK4",
                  "4539 1488 0343 6467", "GB82 WEST 1234 5698 7654 32", "nadia.petrov@marrowgate.io",
                  "+1 415 820 4471", "Nadia Petrov", "18 Larkspur Close", "123-45-6789"]
ORDINARY = ["2026-10-11", "production", "info", "the", "README.md", "v2.4.1", "200", "true", "localhost", "Monday"]


def mode() -> str:
    path = config.HOME / "learning.json"
    try:
        return json.loads(path.read_text()).get("mode", "suggest")
    except (OSError, ValueError):
        return "suggest"


def set_mode(new: str, decisions: int) -> str:
    if new not in ("suggest", "quiet"):
        raise ValueError("mode must be suggest or quiet")
    if new == "quiet" and decisions < UNLOCK:
        raise ValueError(f"Learn quietly unlocks after {UNLOCK} decisions ({decisions} so far)")
    (config.private_dir() / "learning.json").write_text(json.dumps({"mode": new}))
    return new


def _protected(values_of) -> tuple[list[str], list[str]]:
    """(values you want masked: Correct + Missed, values you said were safe: False alarm)."""
    keep, safe = [], []
    for event_id, lab in labels.load().items():
        if lab["label"] == "correct":
            keep += [v["value"] for v in values_of(event_id) if v["value"]]
        elif lab["label"] == "missed":
            keep += [i["value"] for i in lab["items"]]
        elif lab["label"] == "false_alarm":
            safe += [i["value"] for i in lab["items"]]
    return keep, safe


def _hits(rule: dict, value: str) -> bool:
    t = rule["text"]
    try:
        if rule["type"] == "allow":
            return re.fullmatch(t[3:], value) is not None if t.startswith("re:") else t == value
        return re.search(t[3:] if t.startswith("re:") else re.escape(t), value) is not None
    except re.error:
        return True  # a broken pattern never passes


def replay(rule: dict, values_of) -> tuple[bool, str]:
    """(passes, what it found). values_of(event_id) -> [{"value", ...}] from the session vault."""
    keep, safe = _protected(values_of)
    if rule["type"] == "allow":
        if rule["kind"] in userrules.SECRET_KINDS:
            return False, "a secret-type value: waits for your Approve"
        bad = [v for v in keep if _hits(rule, v)] + [v for v in SAFETY_SECRETS if _hits(rule, v)]
        fixed = sum(1 for v in safe if _hits(rule, v))
        if bad:
            return False, f"would unmask {len(bad)} value(s) you or the safety set want hidden"
        return True, f"replay: -{fixed} false alarm(s) on your labels, 0 newly unmasked"
    bad = [v for v in safe if _hits(rule, v)] + [v for v in ORDINARY if _hits(rule, v)]
    caught = sum(1 for v in keep if _hits(rule, v))
    if bad:
        return False, f"would mask {len(bad)} value(s) you or the safety set call safe"
    return True, f"replay: +{caught} caught on your labels, 0 new false alarms"


def after_label(new_rules: list[dict], values_of) -> list[dict]:
    """Learn quietly: apply new suggestions that pass; re-check rules that applied on their own."""
    rules = userrules.load()
    by_id = {r["id"]: r for r in rules}
    changed = []
    for n in new_rules:  # both modes run the test; Suggest shows the result, Learn quietly acts on it
        r = by_id.get(n["id"])
        if r and r["state"] == "suggested":
            ok, note = replay(r, values_of)
            r["replay"] = note
            if ok and mode() == "quiet":
                r.update(state="active", auto=True, decided=None)
                changed.append(r)
    for r in rules:
        if r.get("auto") and r["state"] == "active":
            ok, note = replay(r, values_of)
            if not ok:
                r.update(state="suggested", auto=False, why=r["why"] + f" · paused: {note}")
                changed.append(r)
    if changed or new_rules:
        userrules._save(rules)
    return changed
