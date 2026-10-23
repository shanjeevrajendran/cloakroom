"""`cloakroom report` and the dashboard's Export: counts per day and session, by data kind and action, from the
event log. No values, no placeholder ids, paths reduced to a class (extension and top folder)."""
from __future__ import annotations

import csv
import io
import json
import os
import re

from . import __version__, events

ABOUT = [
    "Cloakroom report: what Cloakroom caught on this machine, counted per day and session by data kind and action.",
    "It holds no values, no placeholder ids and no full paths (a path is shown only as its extension and top folder).",
    "It does not show what Cloakroom missed: a miss leaves no line. It does not cover other agents, machines without "
    "Cloakroom, or prompts you sent with cr! (counted as 'sent anyway', not checked).",
    "It is a record of this tool's actions, not a compliance attestation.",
]
ACTIONS = {"deny": "denied", "block": "denied"}  # the rest keep their own name (masked, withheld, held, allowed once...)
SKIP = {"pass"}  # a tool call checked with nothing found


def path_class(target: str, tool: str = "") -> str:
    """'.py in code' for a file path, the program name for a shell command, '' for none. Never the full path."""
    if not target:
        return ""
    t = re.sub(r"PII_[A-Z]+_\d+", "[masked]", target).replace("\\", "/")
    if tool in ("Bash", "PowerShell"):
        return t.rsplit("/", 1)[-1]  # the program name, never its arguments
    home = os.path.expanduser("~").replace("\\", "/").rstrip("/")
    rel = t[len(home) + 1:] if t.startswith(home + "/") else t.lstrip("/")
    rel = re.sub(r"^[A-Za-z]:/", "", rel)
    parts = [p for p in rel.split("/") if p]
    name = parts[-1] if parts else ""
    ext = (os.path.splitext(name)[1] or (name if name.startswith(".") else "")).lower()  # .env, .npmrc keep their name
    top = parts[0] if len(parts) > 1 else ""
    return f"{ext or 'no extension'}" + (f" in {top}" if top else "")


def rows(since: str = "") -> list[dict]:
    """One row per (day, session, kind, action, path class) with its count, oldest first."""
    counts: dict[tuple, int] = {}
    for ev in events.all_events():
        ts = str(ev.get("ts", ""))
        if since and ts[:10] < since:
            continue
        action = ACTIONS.get(ev.get("action", ""), ev.get("action", ""))
        if not action or action in SKIP or action == "restored":
            continue
        cls = path_class(str(ev.get("target", "")), str(ev.get("tool", ""))) if ev.get("event") != "log" else ""
        kinds = ev.get("kinds") or {"": 1}
        for kind, n in kinds.items():
            key = (ts[:10], str(ev.get("session", "")), kind, action, cls)
            counts[key] = counts.get(key, 0) + (int(n) if kind else 1)
    return [{"day": d, "session": s, "kind": k, "action": a, "path_class": c, "count": n}
            for (d, s, k, a, c), n in sorted(counts.items())]


def export(fmt: str = "csv", since: str = "") -> str:
    chain = events.verify()
    data = rows(since)
    if fmt == "json":
        return json.dumps({"about": ABOUT, "version": __version__, "since": since or None,
                           "chain": {"ok": chain["ok"], "head": chain["head"], "lines": chain["lines"]},
                           "rows": data}, indent=1) + "\n"
    out = io.StringIO()
    for line in ABOUT:
        out.write(f"# {line}\n")
    out.write(f"# cloakroom {__version__}; log chain {'intact' if chain['ok'] else 'BROKEN at ' + chain['broken']}, "
              f"head {chain['head']}\n")
    w = csv.DictWriter(out, fieldnames=["day", "session", "kind", "action", "path_class", "count"], lineterminator="\n")
    w.writeheader()
    w.writerows(data)
    return out.getvalue()


def verify_text() -> str:
    r = events.verify()
    if r["ok"]:
        return (f"log chain intact: {r['lines']} lines checked\nhead {r['head']}\n"
                "(keep the head hash somewhere else: a later check that doesn't lead to it means the log was rewritten)")
    return f"log chain BROKEN at {r['broken']} after {r['lines']} good lines\nlast good hash {r['head']}"
