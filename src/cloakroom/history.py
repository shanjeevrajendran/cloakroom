"""`cloakroom clean-history`: find keys and passwords already sitting in old Claude Code transcripts
(~/.claude/projects/**/*.jsonl) and the prompt history (~/.claude/history.jsonl), and on your yes replace them.

The first run is always a dry run. Files changed in the last IN_USE_MINUTES are skipped: Claude Code may have them
open (the current session's transcript among them). Before a file is rewritten its original goes to a backup folder
you delete when you're sure. Counts only, never values.

Also run on its own for the current session (`scrub_session`, called by the Stop and SessionEnd hooks and when a
prompt is stopped): that session's transcript only, no backup (it would keep the values), and a file Claude Code is
writing is rewritten only if it did not change meanwhile.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from . import config, decode, engine
from .vault import Vault

KINDS = {"SECRET", "PASSWORD"}
IN_USE_MINUTES = 10
MAX_BYTES = 200 * 1024 * 1024  # a larger file is skipped, not loaded


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def files(root: Path) -> list[tuple[str, Path]]:
    """(project, path) for every transcript and history file."""
    out = []
    projects = root / "projects"
    if projects.is_dir():
        for p in sorted(projects.rglob("*.jsonl")):
            out.append((p.relative_to(projects).parts[0], p))
    if (root / "history.jsonl").is_file():
        out.append(("(prompt history)", root / "history.jsonl"))
    return out


def _known() -> dict[str, str]:
    """Keys and passwords Cloakroom has seen in any session, plus your exact always-mask entries of those kinds."""
    cfg = config.load()
    known = {v: k for v, k in engine.known_values(None, cfg["always"]).items() if k in KINDS}
    sessions = config.HOME / "sessions"
    for path in sorted(sessions.glob("*.json")) if sessions.is_dir() else []:
        try:
            known.update({v: k for v, k in engine.known_values(Vault(path)).items() if k in KINDS})
        except Exception:
            continue
    return known


def _clean_text(text: str, cfg: dict, known: dict, always: list, exact: dict | None = None,
                tally: dict | None = None, detect: bool = True) -> tuple[str, int]:
    """exact: {value: kind} replaced wherever they appear, any kind. tally: {kind: count}, filled when given."""
    tally = tally if tally is not None else {}
    n = 0
    for value, kind in (exact or {}).items():
        if value in text:
            c = text.count(value)
            text, n, tally[kind] = text.replace(value, f"[{kind} removed by Cloakroom]"), n + c, tally.get(kind, 0) + c
    hits = []
    if detect:
        hits, _ = engine.detect(text, "rules", cfg["allow"], always, known=known)
        hits = [f for f in hits if f.kind in KINDS]
    for f in sorted(hits, key=lambda f: f.start, reverse=True):
        text = text[:f.start] + f"[{f.kind} removed by Cloakroom]" + text[f.end:]
        tally[f.kind] = tally.get(f.kind, 0) + 1
    return text, n + len(hits)


def _walk(value, fn):
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {k: _walk(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, fn) for v in value]
    return value


def scan_file(path: Path, cfg: dict, known: dict, always: list, rewrite: bool, exact: dict | None = None,
              tally: dict | None = None, detect: bool = True) -> tuple[int, list[str] | None]:
    """(values found, the new lines when rewrite and something was found). Each line is JSON: only string fields
    change, so the file stays valid."""
    found, out = 0, []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f.read().splitlines():
            n = [0]

            def fn(s):
                if len(s) < 8:
                    return s
                new, k = _clean_text(s, cfg, known, always, exact, tally, detect)
                n[0] += k
                return new
            try:
                obj = json.loads(line)
            except ValueError:
                obj = None
            if obj is None:
                new = line
                if line.strip():
                    new, k = _clean_text(line, cfg, known, always, exact, tally, detect)
                    n[0] += k
            else:
                cleaned = _walk(obj, fn)
                new = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")) if n[0] else line
            found += n[0]
            out.append(new)
    return found, (out if rewrite and found else None)


def run(dry_run: bool = False, yes: bool = False, root: Path | None = None, ask=input, say=print) -> int:
    root = root or claude_dir()
    marker = config.private_dir() / "clean-history.dry-run-done"
    first = not marker.exists()
    if first:
        dry_run = True
    cfg = config.load()
    known = _known()
    always = [(e, k) for e, k in cfg["always"] if k in KINDS]
    now = time.time()
    per_project: dict[str, int] = {}
    todo, skipped_busy, skipped_big = [], 0, 0
    for project, path in files(root):
        st = path.stat()
        if now - st.st_mtime < IN_USE_MINUTES * 60:
            skipped_busy += 1
            continue
        if st.st_size > MAX_BYTES:
            skipped_big += 1
            continue
        n, _ = scan_file(path, cfg, known, always, rewrite=False)
        if n:
            per_project[project] = per_project.get(project, 0) + n
            todo.append(path)
    total = sum(per_project.values())
    say(f"Cloakroom clean-history: {total} key(s) or password(s) in {len(todo)} file(s) under {root}")
    for project, n in sorted(per_project.items()):
        say(f"  {project}: {n}")
    if skipped_busy:
        say(f"  skipped {skipped_busy} file(s) changed in the last {IN_USE_MINUTES} min (Claude Code may have them open)")
    if skipped_big:
        say(f"  skipped {skipped_big} file(s) over {MAX_BYTES // (1024 * 1024)} MB")
    if first:
        marker.touch(mode=0o600)
    if dry_run or not todo:
        if todo:
            say("Dry run: nothing changed." + (" Run `cloakroom clean-history` again to clean them." if first else ""))
        return 0
    if not yes and ask(f"Replace them in {len(todo)} file(s)? Originals go to a backup folder first. [y/N] ").strip().lower() not in ("y", "yes"):
        say("Nothing changed.")
        return 0
    backup = config.private_dir("history-backup") / time.strftime("%Y%m%d-%H%M%S")
    backup.mkdir(mode=0o700)
    done = 0
    for path in todo:
        if time.time() - path.stat().st_mtime < IN_USE_MINUTES * 60:  # became busy while we asked
            continue
        n, lines = scan_file(path, cfg, known, always, rewrite=True)
        if not lines:
            continue
        rel = path.relative_to(root)
        dest = backup / rel
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(path, dest)
        os.chmod(dest, 0o600)
        tmp = path.with_name(path.name + ".cloakroom-tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
        done += n
    say(f"Replaced {done} value(s). Originals: {backup} (they still hold the values: delete the folder when you're sure).")
    return 0


# --- the current session (hook.py) ---

def _pending(session: str) -> Path:
    return config.vault_path(session).with_suffix(".scrub")


def mark_stopped(session: str, placeholders: list[str]):
    """Remember the placeholders of a stopped prompt (not their values: those stay in the session's vault) so the next
    Stop and the session end can take them out of the transcript, whenever Claude Code writes the prompt there."""
    with open(_pending(session), "a", encoding="utf-8") as f:
        f.write("".join(p + "\n" for p in placeholders))
    os.chmod(_pending(session), 0o600)


def clear_stopped(session: str):
    _pending(session).unlink(missing_ok=True)


def own_file(transcript, root: Path | None = None) -> Path | None:
    """The transcript hook input names, only if it is a .jsonl file under <claude dir>/projects."""
    try:
        path = Path(str(transcript)).resolve(strict=True)
        base = ((root or claude_dir()) / "projects").resolve()
    except (OSError, ValueError):
        return None
    return path if path.is_file() and path.suffix == ".jsonl" and base in path.parents else None


def _stopped_values(session: str) -> dict[str, str]:
    """{value: kind} for the stopped prompts' placeholders, looked up in the session's vault."""
    try:
        names = set(_pending(session).read_text(encoding="utf-8").split())
    except OSError:
        return {}
    by_ph = Vault(config.vault_path(session)).by_placeholder
    return {by_ph[p]: p.split("_")[1] for p in names if p in by_ph and len(by_ph[p]) >= decode.KNOWN_MIN}


def scrub_session(transcript, session: str, full: bool) -> tuple[dict, str]:
    """Take the session's stopped-prompt values out of its own transcript. full: also every key and password
    Cloakroom or the rules find in it (session end); otherwise only the stopped values. Returns ({kind: count}, folder) and rewrites nothing when there is
    nothing to take out, the setting is off, or the file keeps changing (the next stop tries again)."""
    cfg = config.load()
    path = own_file(transcript)
    if not path or not cfg.get("scrub_transcript", True):
        return {}, ""
    exact = _stopped_values(session)
    known, always = {}, []
    if full:
        vault = Vault(config.vault_path(session))
        known = {v: k for v, k in engine.known_values(vault, cfg["always"]).items() if k in KINDS}
        always = [(e, k) for e, k in cfg["always"] if k in KINDS]
    elif not exact:
        return {}, ""
    for _ in range(3):
        st = path.stat()
        if st.st_size > MAX_BYTES:
            return {}, ""
        tally: dict[str, int] = {}
        _, lines = scan_file(path, cfg, known, always, True, exact, tally, detect=full)
        if not lines:
            return {}, ""
        tmp = path.with_name(path.name + ".cloakroom-tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        shutil.copymode(path, tmp)
        now = path.stat()
        # shortcut: a line appended between this check and the replace is lost; upgrade with a lock Claude Code honours
        if (now.st_size, now.st_mtime_ns) == (st.st_size, st.st_mtime_ns):
            os.replace(tmp, path)
            return tally, str(claude_dir())
        tmp.unlink(missing_ok=True)  # Claude Code wrote meanwhile: read it again
    return {}, ""


def summary(tally: dict, root: str) -> str:
    """clean-history's report line, for one file."""
    what = "key(s) or password(s)" if set(tally) <= KINDS else "value(s)"
    return f"Cloakroom clean-history: {sum(tally.values())} {what} in 1 file(s) under {root}"
