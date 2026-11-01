"""`cloakroom exposure`: which keys and passwords your coding agents saw, so you know what to rotate.

Two parts:
  Seen with Cloakroom: from the event log (events.py), per session and agent: kind, where (file read, command,
    prompt), what Cloakroom did, and a hint looked up from the session vault.
  Found in agent history: a read-only scan of old transcripts (Claude Code, Codex CLI, Gemini CLI, Cursor's command-line agent) with the same
    rules clean-history uses: keys and passwords sitting there from before Cloakroom, or that it missed.
Never a value: a hint is a well-known public prefix (sk-, ghp_, AKIA...) and, for values of 16+ characters, the last
4, which is what key dashboards show. Lists only what Cloakroom detected: a key it missed leaves no line.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from . import agents, config, engine, events, history, report
from .vault import Vault

KINDS = history.KINDS
SKIP_ACTIONS = {"pass", "restored", "skipped", "pruned", ""}
# Public prefixes that name a key's issuer; the hint never shows more of the value than these and the last 4
PREFIX = re.compile(r"^(?:github_pat_|glpat-|sk-ant-|sk-proj-|sk_live_|sk_test_|rk_live_|xox[abprs]-|gh[pousr]_|"
                    r"sk-|AKIA|ASIA|AIza|hf_|npm_|pypi-|SG\.|dop_v1_|xai-|gsk_)")
TAIL_MIN = 16  # shorter values show no characters at all
MISSED = ("Lists only what Cloakroom detected. A key or password it missed leaves no line, so a key that is not "
          "listed is not proof it is safe.")


def hint(value: str) -> str:
    m = PREFIX.match(value)
    head = m.group() if m else ""
    tail = value[-4:] if len(value) >= TAIL_MIN else ""
    return f"{head}…{tail}" + ("" if tail else f" ({len(value)} chars)")


def fingerprint(value: str) -> str:
    """Groups one key across sessions and files; never printed in text output."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _where(ev: dict) -> str:
    tool, target = str(ev.get("tool", "")), str(ev.get("target", ""))
    if ev.get("event") == "prompt" or not tool:
        return "prompt"
    cls = report.path_class(target, tool)
    if tool in ("Bash", "PowerShell"):
        return f"command {cls}".strip()
    return f"{tool} {cls}".strip()


def _vaults() -> dict:
    cache: dict[str, dict] = {}

    def values(session: str) -> dict:
        if session not in cache:
            try:
                v = Vault(config.vault_path(session)).by_placeholder
            except Exception:
                v = {}
            cache[session] = v
        return cache[session]
    return values


def seen() -> list[dict]:
    """One row per (session, agent, kind, where, action, key), oldest first."""
    vault = _vaults()
    rows: dict[tuple, dict] = {}
    for ev in events.all_events():
        action = report.ACTIONS.get(ev.get("action", ""), ev.get("action", ""))
        if ev.get("event") in ("skipped", "log") or action in SKIP_ACTIONS:
            continue
        kinds = {k: int(n) for k, n in (ev.get("kinds") or {}).items() if k in KINDS}
        if not kinds:
            continue
        session, ts = str(ev.get("session", "")), str(ev.get("ts", ""))
        values = vault(session)
        keys: dict[str, list] = {k: [] for k in kinds}  # kind -> values found by placeholder
        for ph in ev.get("placeholders") or []:
            kind = ph.split("_")[1] if ph.count("_") >= 2 else ""
            if kind in keys and ph in values:
                keys[kind].append(values[ph])
        for kind, n in kinds.items():
            vals = keys[kind] or [None] * n  # no placeholder kept (denied, withheld): count without a hint
            for val in vals:
                key = (session, str(ev.get("agent") or "unknown"), kind, _where(ev), action,
                       fingerprint(val) if val else "")
                row = rows.setdefault(key, {"session": key[0], "agent": key[1], "kind": kind, "where": key[3],
                                            "action": action, "hint": hint(val) if val else "not kept",
                                            "fingerprint": key[5], "count": 0, "first": ts, "last": ts})
                row["count"] += 1
                row["last"] = max(row["last"], ts)
    return sorted(rows.values(), key=lambda r: (r["first"], r["session"], r["kind"]))


def history_files() -> list[tuple[str, Path]]:
    """(agent, path) for each agent's transcripts that exist on this machine."""
    out = [("claude", p) for _, p in history.files(history.claude_dir())]
    codex = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    for sub in ("sessions", "archived_sessions"):
        if (codex / sub).is_dir():
            out += [("codex", p) for p in sorted((codex / sub).rglob("*.jsonl"))]
    if (codex / "history.jsonl").is_file():
        out.append(("codex", codex / "history.jsonl"))
    gemini = Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini" / "tmp"
    if gemini.is_dir():
        out += [("gemini", p) for p in sorted(gemini.rglob("*.json*")) if p.suffix in (".json", ".jsonl")]
    cursor = Path(agents.cursor_home()) / "projects"
    out += [("cursor", p) for p in sorted(cursor.glob("*/agent-transcripts/**/*.jsonl"))]
    return out


def _strings(path: Path):
    """Every string in a JSON or JSON-lines file (a line that isn't JSON counts as one string). Read only."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    try:
        docs = [json.loads(text)] if path.suffix == ".json" else None
    except ValueError:
        docs = None
    if docs is None:
        docs = []
        for line in text.splitlines():
            try:
                docs.append(json.loads(line))
            except ValueError:
                docs.append(line)
    stack = docs
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            if len(v) >= 8:
                yield v
        elif isinstance(v, dict):
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)


def found(since: str = "") -> tuple[list[dict], dict]:
    """Rows per (file, key) from old transcripts, and what was skipped. Never writes."""
    cfg = config.load()
    known = history._known()
    always = [(e, k) for e, k in cfg["always"] if k in KINDS]
    rows, skipped = [], {"busy": 0, "big": 0, "unreadable": 0}
    home, now = str(Path.home()), time.time()
    for agent, path in history_files():
        try:
            st = path.stat()
        except OSError:
            continue
        # shortcut: the date is the file's last change, not each line's; read line timestamps if users need it
        day = time.strftime("%Y-%m-%d", time.localtime(st.st_mtime))
        if since and day < since:
            continue
        if now - st.st_mtime < history.IN_USE_MINUTES * 60:
            skipped["busy"] += 1
            continue
        if st.st_size > history.MAX_BYTES:
            skipped["big"] += 1
            continue
        per: dict[str, dict] = {}
        try:
            for s in _strings(path):
                hits, _ = engine.detect(s, "rules", cfg["allow"], always, known=known)
                for f in hits:
                    if f.kind not in KINDS:
                        continue
                    val = s[f.start:f.end]
                    fp = fingerprint(val)
                    row = per.setdefault(fp, {"agent": agent, "kind": f.kind, "hint": hint(val), "fingerprint": fp,
                                              "file": "~" + str(path)[len(home):] if str(path).startswith(home)
                                              else str(path), "date": day, "count": 0})
                    row["count"] += 1
        except OSError:
            skipped["unreadable"] += 1
            continue
        rows += per.values()
    return sorted(rows, key=lambda r: (r["date"], r["file"])), skipped


def build(since: str = "") -> dict:
    s = [r for r in seen() if not since or r["first"][:10] >= since]
    h, skipped = found(since)
    rotate: dict[str, dict] = {}
    for r in s + h:
        if not r["fingerprint"]:
            continue
        x = rotate.setdefault(r["fingerprint"], {"kind": r["kind"], "hint": r["hint"], "fingerprint": r["fingerprint"],
                                                 "places": 0, "last": ""})
        x["places"] += 1
        x["last"] = max(x["last"], (r.get("last") or r.get("date") or "")[:10])
    unhinted = sum(r["count"] for r in s if not r["fingerprint"])
    return {"about": MISSED, "seen": s, "history": h, "skipped": skipped, "unhinted": unhinted,
            "rotate": sorted(rotate.values(), key=lambda x: x["last"], reverse=True)}


def render(data: dict, fmt: str = "text") -> str:
    if fmt == "json":
        return json.dumps(data, indent=1) + "\n"
    out = ["Cloakroom exposure: keys and passwords your coding agents saw. Hints only, never values.", ""]
    out.append(f"Seen with Cloakroom ({len(data['seen'])}):")
    for r in data["seen"]:
        out.append(f"  {r['first'][:10]}  {r['agent']:<7} session {r['session'][:12]:<12} {r['kind']:<8} "
                   f"{r['hint']:<20} {r['where']}: {r['action']}" + (f" x{r['count']}" if r["count"] > 1 else ""))
    if not data["seen"]:
        out.append("  none in the event log")
    out += ["", f"Found in agent history files, from before Cloakroom or missed by it ({len(data['history'])}):"]
    for r in data["history"]:
        out.append(f"  {r['date']}  {r['agent']:<7} {r['kind']:<8} {r['hint']:<20} {r['file']}")
    if not data["history"]:
        out.append("  none")
    sk = data["skipped"]
    if sk["busy"]:
        out.append(f"  skipped {sk['busy']} file(s) changed in the last {history.IN_USE_MINUTES} min (still in use)")
    if sk["big"]:
        out.append(f"  skipped {sk['big']} file(s) over {history.MAX_BYTES // (1024 * 1024)} MB")
    if sk["unreadable"]:
        out.append(f"  skipped {sk['unreadable']} unreadable file(s)")
    out += ["", f"Rotate these ({len(data['rotate'])}):"]
    for x in data["rotate"]:
        out.append(f"  [ ] {x['kind']:<8} {x['hint']:<20} seen in {x['places']} place(s), last {x['last']}")
    if data["unhinted"]:
        out.append(f"  [ ] {data['unhinted']} more Cloakroom stopped before it kept a hint (denied or withheld): "
                   "check the sessions above")
    if data["history"]:
        out.append("  Then `cloakroom clean-history` removes them from the transcripts.")
    out += ["", data["about"], ""]
    return "\n".join(out)
