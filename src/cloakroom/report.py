"""`cloakroom report` and the dashboard's Export: counts per day and session, by data kind and action, from the
event log. No values, no placeholder ids, paths reduced to a class (extension and top folder)."""
from __future__ import annotations

import csv
import io
import json
import os
import re

from . import __version__, events
from .vault import ANY_PLACEHOLDER

ABOUT = [
    "Cloakroom report: what Cloakroom caught on this machine, counted per day and session by data kind and action.",
    "It holds no values, no placeholder ids and no full paths (a path is shown only as its extension and top folder).",
    "It does not show what Cloakroom missed: a miss leaves no line. It does not cover other agents, machines without "
    "Cloakroom, or prompts you sent with cr! (counted as 'sent anyway', not checked).",
    ("Checks skipped: calls Cloakroom let through without a full check (too large, too slow, git error, model not "
     "ready), counted by check and reason."),
    "It is a record of this tool's actions, not a compliance attestation.",
]
DEST_ABOUT = [
    "Cloakroom destinations: where the commands and web or MCP calls Cloakroom checked sent data, counted per host.",
    "Kinds are the data kinds those calls carried (placeholders and anything held); no values, no addresses beyond the host.",
    "It does not see the agent's own calls to its model provider, or tools that run no Cloakroom check.",
]
ACTIONS = {"deny": "denied", "block": "denied"}  # the rest keep their own name (masked, withheld, held, allowed once...)
SKIP = {"pass"}  # a tool call checked with nothing found


def path_class(target: str, tool: str = "") -> str:
    """'.py in code' for a file path, the program name for a shell command, '' for none. Never the full path."""
    if not target:
        return ""
    t = ANY_PLACEHOLDER.sub("[masked]", target).replace("\\", "/")
    if tool in ("Bash", "PowerShell"):
        return t.rsplit("/", 1)[-1]  # the program name, never its arguments
    home = os.path.expanduser("~").replace("\\", "/").rstrip("/")
    rel = t[len(home) + 1:] if t.startswith(home + "/") else t[2:] if t.startswith("~/") else t.lstrip("/")
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
        if ev.get("event") == "skipped" or not action or action in SKIP or action == "restored":
            continue
        cls = path_class(str(ev.get("target", "")), str(ev.get("tool", ""))) if ev.get("event") != "log" else ""
        kinds = ev.get("kinds") or {"": 1}
        for kind, n in kinds.items():
            key = (ts[:10], str(ev.get("session", "")), kind, action, cls)
            counts[key] = counts.get(key, 0) + (int(n) if kind else 1)
    return [{"day": d, "session": s, "kind": k, "action": a, "path_class": c, "count": n}
            for (d, s, k, a, c), n in sorted(counts.items())]


def skipped(since: str = "") -> list[dict]:
    """Checks skipped (fail-open), counted by check and reason, most first."""
    counts: dict[tuple, int] = {}
    for ev in events.all_events():
        if ev.get("event") != "skipped" or (since and str(ev.get("ts", ""))[:10] < since):
            continue
        key = (str(ev.get("check", "")), str(ev.get("reason", "")), events.skip_why(ev))
        counts[key] = counts.get(key, 0) + 1
    return [{"check": c, "reason": r, "why": w, "count": n}
            for (c, r, w), n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]


def destinations(since: str = "") -> list[dict]:
    """Where outgoing calls went: per host, the calls, how many were held, and the data kinds they carried (counts).
    Most calls first. Hosts and kinds only, never values or addresses."""
    hosts: dict[str, dict] = {}
    for ev in events.all_events():
        if not ev.get("dest") or (since and str(ev.get("ts", ""))[:10] < since):
            continue
        for host in ev["dest"]:
            row = hosts.setdefault(str(host), {"host": str(host), "calls": 0, "held": 0, "kinds": {}})
            row["calls"] += 1
            row["held"] += ev.get("action") == "held"
            for kind, n in (ev.get("carries") or {}).items():
                row["kinds"][kind] = row["kinds"].get(kind, 0) + int(n)
    return sorted(hosts.values(), key=lambda r: (-r["calls"], r["host"]))


def destinations_text(fmt: str = "csv", since: str = "") -> str:
    data = destinations(since)
    if fmt == "json":
        return json.dumps({"about": DEST_ABOUT, "version": __version__, "since": since or None, "destinations": data},
                          indent=1) + "\n"
    out = io.StringIO()
    for line in DEST_ABOUT:
        out.write(f"# {line}\n")
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["host", "calls", "held", "kinds"])
    for r in data:
        w.writerow([r["host"], r["calls"], r["held"], " ".join(f"{k}x{n}" for k, n in sorted(r["kinds"].items()))])
    return out.getvalue()


def export(fmt: str = "csv", since: str = "") -> str:
    chain = events.verify()
    data, skips = rows(since), skipped(since)
    if fmt == "json":
        return json.dumps({"about": ABOUT, "version": __version__, "since": since or None,
                           "chain": {"ok": chain["ok"], "head": chain["head"], "lines": chain["lines"]},
                           "rows": data, "skipped": skips}, indent=1) + "\n"
    out = io.StringIO()
    for line in ABOUT:
        out.write(f"# {line}\n")
    out.write(f"# cloakroom {__version__}; log chain {'intact' if chain['ok'] else 'BROKEN at ' + chain['broken']}, "
              f"head {chain['head']}\n")
    out.write(f"# Checks skipped: {sum(x['count'] for x in skips)}\n")
    for x in skips:
        out.write(f"#   {x['count']} x {x['check']}: {x['why']}\n")
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
