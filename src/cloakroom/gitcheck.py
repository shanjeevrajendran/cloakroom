"""The check before a git commit or push an agent runs: do the added lines carry a key or a password?

_git_steps() reads the command (following cd and git -C), git_added() asks git what the commit or push would
record and scans the added lines with the rules, your always-mask entries and the session's known secrets, and
_git_hold() holds the command when they carry one. It fails open, and says so, on a git error, on a change set too
large to scan and after GIT_SECONDS. git_added() is also your own pre-commit hook's check (githook.py).
"""
from __future__ import annotations

import os
import re
import sys
import time

from . import config, engine, shell
from .hookcall import FOUND, HOLD_MINUTES, hold, skip, summary
from .vault import Vault

NEW_FILE_MAX = 4 * 512 * 1024  # a new file larger than this is not read (4 x hook.PRIME_MAX_BYTES)
COMMIT_KINDS = {"SECRET", "PASSWORD"}  # kinds kept out of files git would commit; personal data is not checked


def _git_would_commit(path: str) -> bool:
    """The file is in a git repo and not ignored. Any doubt (no git, not a repo, slow) answers False: restore as before."""
    import subprocess
    d = os.path.dirname(path)
    while d and not os.path.isdir(d):
        d = os.path.dirname(d) if os.path.dirname(d) != d else ""
    if not d:
        return False
    try:
        r = subprocess.run(["git", "-C", d, "check-ignore", "-q", "--", path], capture_output=True, timeout=1)
    except subprocess.TimeoutExpired:
        skip("too-slow", "git check on a write holding a key",
              "whether git would commit this write, which holds a key", ms=1000, limit=1)
        return False
    except Exception:  # no git: nothing can commit the file
        return False
    return r.returncode == 1  # 0 ignored, 1 not ignored, 128 not a repo or error


GIT_SECONDS = 2.0  # the commit and push check gives up (lets the command run) after this long
GIT_MAX_TEXT = 4 * 1024 * 1024  # added lines scanned at most; a bigger change set fails open
_COMMIT_VALUE_OPTS = {"-m", "--message", "-F", "--file", "-c", "-C", "--reuse-message", "--reedit-message",
                      "--author", "--date", "-t", "--template", "--fixup", "--squash", "--cleanup", "--trailer"}
_PUSH_VALUE_OPTS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec", "--signed"}


def _git_bash_path(p: str, powershell: bool) -> str:
    """Git Bash on Windows writes C:\\Users\\x as /c/Users/x; git.exe and Python need the drive form."""
    m = None if powershell or sys.platform != "win32" else re.fullmatch(r"/([A-Za-z])(/.*)?", p)
    return f"{m.group(1).upper()}:" + (m.group(2) or "/").replace("/", "\\") if m else p


def _add_specs(args: list[str], folder: str) -> tuple[list[str], list[str]]:
    """What a git add stages, as pathspecs: (for changed tracked files, for new files). Paths are made absolute
    (git takes those inside the work tree); ":/" is the whole repo."""
    paths, everything, tracked_only, rest = [], False, False, False
    for a in args:
        if rest or not a.startswith("-"):
            paths.append(os.path.normpath(os.path.join(folder, os.path.expanduser(a))))
        elif a == "--":
            rest = True
        elif a.startswith("--pathspec-from-file"):  # paths unknown here: the whole repo
            return [":/"], [":/"]
        elif a in ("-A", "--all", "--no-ignore-removal"):
            everything = True
        elif a in ("-u", "--update"):
            tracked_only = True
    specs = paths or ([":/"] if everything or tracked_only else [])
    return specs, [] if tracked_only else specs


def _git_steps(command: str, cwd: str, powershell: bool) -> list[tuple[str, str, list[str], tuple | None]]:
    """(commit|push, repo folder, its arguments, what git adds before it staged: _add_specs, or None) for each
    git commit or push in the command, following cd and git -C."""
    import shlex
    out, here, added = [], cwd, None
    for seg in (shell.PS_SEGMENT_SPLIT if powershell else shell.SEGMENT_SPLIT).split(command):
        try:
            words = shlex.split(seg, posix=not powershell)
        except ValueError:
            words = seg.split()
        words = [w.strip("\"'") for w in words]
        while words and (re.fullmatch(r"\w+=.*", words[0]) or words[0] in ("sudo", "env", "command", "exec")):
            words = words[1:]
        if not words:
            continue
        if words[0].lower() in ("cd", "pushd", "set-location", "sl") and len(words) > 1:
            here = os.path.join(here, os.path.expanduser(_git_bash_path(words[-1], powershell)))
            continue
        if re.split(r"[\\/]", words[0])[-1].lower() not in ("git", "git.exe"):
            continue
        i, repo = 1, here
        while i < len(words) and words[i].startswith("-"):
            if words[i] == "-C" and i + 1 < len(words):
                repo = os.path.join(repo, os.path.expanduser(_git_bash_path(words[i + 1], powershell)))
                i += 1
            elif words[i] == "-c":
                i += 1
            i += 1
        sub = words[i] if i < len(words) else ""
        if sub == "add":
            tracked, new = _add_specs(words[i + 1:], repo)
            added = (added[0] + tracked, added[1] + new) if added else (tracked, new)
        elif sub in ("commit", "push"):
            out.append((sub, repo, words[i + 1:], added))
    return out


def _commit_diffs(args: list[str], added: tuple | None) -> list[list[str]]:
    """git commands whose output is what a commit with these arguments would record (a superset is fine).
    added: what git adds earlier in the command stage (_add_specs), which hasn't run yet when this is checked."""
    widen, i = False, 0
    while i < len(args):
        a = args[i]
        if a in _COMMIT_VALUE_OPTS:
            i += 1
        elif a in ("-a", "--all", "--include", "-i", "--only", "-o"):
            widen = True
        elif a.startswith("-") and not a.startswith("--"):
            for j, c in enumerate(a[1:]):
                if c == "a":
                    widen = True
                if c in "mFcCt":  # the rest of the cluster (or the next word) is that option's value
                    if j == len(a) - 2:
                        i += 1
                    break
        elif a == "--":
            widen = widen or i + 1 < len(args)
            break
        elif not a.startswith("-"):
            widen = True  # git commit <paths>
        i += 1
    cmds = [["diff", "--cached"]]
    if widen:
        cmds.append(["diff", "HEAD"])
    elif added and added[0]:
        cmds.append(["diff", "HEAD", "--", *added[0]])
    if added and added[1]:
        cmds.append(["untracked", *added[1]])
    return cmds


def _push_logs(args: list[str]) -> list[list[str]]:
    srcs, positional, everything, i = [], [], False, 0
    while i < len(args):
        a = args[i]
        if a in _PUSH_VALUE_OPTS:
            i += 1
        elif a in ("--all", "--mirror", "--branches"):
            everything = True
        elif not a.startswith("-"):
            positional.append(a)
        i += 1
    for ref in positional[1:]:
        src = ref.lstrip("+").split(":")[0]
        if src:
            srcs.append(src)
    srcs = ["--branches"] if everything else srcs or ["HEAD"]
    return [["log", "-p", "--format=", *srcs, "--not", "--remotes"]]


def _added_lines(diff: str) -> dict[str, list[str]]:
    """{file: added lines} from git diff or git log -p output."""
    out: dict[str, list[str]] = {}
    current, header = "", False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            header, current = True, ""
            continue
        if header:
            if line.startswith("+++ "):
                p = line[4:].strip().strip('"')
                current = p[2:] if p.startswith("b/") else ("" if p == "/dev/null" else p)
            elif line.startswith("@@"):
                header = False
            continue
        if current and line.startswith("+"):
            out.setdefault(current, []).append(line[1:])
    return out


def _in_repo(folder: str) -> bool:
    import subprocess
    try:
        return subprocess.run(["git", "-C", folder, "rev-parse", "--git-dir"], capture_output=True,
                              timeout=1).returncode == 0
    except Exception:
        return False


def _git_out(repo: str, deadline: float, *argv) -> str:
    import subprocess
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError
    r = subprocess.run(["git", "-C", repo, "-c", "core.quotePath=false", *argv], capture_output=True, timeout=left)
    if r.returncode != 0:
        raise RuntimeError("git")
    return r.stdout.decode("utf-8", "replace")


def _added_files(repo: str, argvs: list[list[str]], deadline: float) -> dict[str, list[str]]:
    """{file: the lines git would add to it} for the git commands in argvs (_commit_diffs, _push_logs): the added
    lines of each diff or log, and the whole of each new file a git add brings in. A new file larger than
    NEW_FILE_MAX is left out, and a "too-large" skip is noted. Running out of time raises TimeoutError; a git error
    raises."""
    files: dict[str, list[str]] = {}
    root = ""
    for argv in argvs:
        specs = argv[3:] if argv[:3] == ["diff", "HEAD", "--"] else argv[1:] if argv[0] == "untracked" else None
        if specs:  # a git add's paths: only those in this repo (cd a && git add x && cd ../b && git commit)
            root = root or os.path.realpath(_git_out(repo, deadline, "rev-parse", "--show-toplevel").strip())
            specs = [s for s in specs if s == ":/" or os.path.realpath(s) == root
                     or os.path.realpath(s).startswith(root + os.sep)]
            if not specs:
                continue
        if argv[0] == "untracked":
            root = root or os.path.realpath(_git_out(repo, deadline, "rev-parse", "--show-toplevel").strip())
            for name in _git_out(repo, deadline, "ls-files", "--full-name", "--others", "--exclude-standard", "-z",
                                 "--", *(specs or [":/"])).split("\0"):
                path = os.path.join(root, name)
                if not (name and os.path.isfile(path)):
                    continue
                if os.path.getsize(path) > NEW_FILE_MAX:
                    skip("too-large", "new file in a git commit", "a new file in this git commit",
                          size=os.path.getsize(path), limit=NEW_FILE_MAX)
                    continue
                with open(path, encoding="utf-8", errors="replace") as f:
                    files.setdefault(name, []).extend(f.read().splitlines())
            continue
        if argv[:2] == ["diff", "HEAD"]:
            try:
                out = _git_out(repo, deadline, "diff", "HEAD", "--no-color", "--no-ext-diff", "-U0", *argv[2:3],
                               *(specs or []))
            except RuntimeError:  # no commit yet: the staged diff covers it
                continue
        else:
            out = _git_out(repo, deadline, *argv, "--no-color", "--no-ext-diff", "-U0")
        for name, lines in _added_lines(out).items():
            files.setdefault(name, []).extend(lines)
    return files


def git_added(repo: str, argvs: list[list[str]], vault: Vault | None, cfg: dict,
              deadline: float, check: str = "git commit") -> list[tuple[str, str, str]] | None:
    """(file, placeholder or "", kind) for each key or password the added lines carry (the rules, your always-mask
    entries and the session's known secrets). argvs: git commands (_commit_diffs, _push_logs). None when the change
    set is too large to scan (fail open, a "too-large" skip is noted for check); running out of time raises
    TimeoutError and a git error raises. Also the git hook's check (githook.py)."""
    from fnmatch import fnmatch
    known = {v: k for v, k in engine.known_values(vault, cfg["always"]).items() if k in COMMIT_KINDS}
    always = [(e, k) for e, k in cfg["always"] if k in COMMIT_KINDS]
    globs = cfg.get("allow_secret_paths") or []
    files = _added_files(repo, argvs, deadline)
    size = sum(len(x) for lines in files.values() for x in lines)
    if size > GIT_MAX_TEXT:
        skip("too-large", check, size=size, limit=GIT_MAX_TEXT)
        return None
    found = []
    for name, lines in files.items():
        if time.monotonic() > deadline:
            raise TimeoutError
        if any(fnmatch(name, g) or fnmatch(os.path.basename(name), g) for g in globs):
            continue
        text = "\n".join(lines)
        hits, _ = engine.detect(text, "rules", cfg["allow"], always, known=known)
        for f in hits:
            if f.kind in COMMIT_KINDS:
                found.append((name, vault.by_value.get(f.value(text), "") if vault else "", f.kind))
    return found


def _git_hold(command: str, cwd: str, vault: Vault, powershell: bool) -> dict | None:
    """Hold a git commit or push whose changes carry a known secret or a key the rules recognise, like the egress
    hold (cr: allow lets it run once). Names the file and placeholder or kind, never the value. Fails open on any
    git error, on a change set too large to scan, or after GIT_SECONDS."""
    import subprocess
    steps = _git_steps(command, cwd, powershell)
    if not steps:
        return None
    deadline = time.monotonic() + GIT_SECONDS
    cfg = config.load()
    found, verb, sub, repo = [], "", steps[0][0], steps[0][1]
    try:
        for sub, repo, args, added in steps:
            argvs = _commit_diffs(args, added) if sub == "commit" else _push_logs(args)
            found = git_added(repo, argvs, vault, cfg, deadline, f"git {sub}")
            if found is None:
                return None
            if found:
                verb = sub
                break
    except (TimeoutError, subprocess.TimeoutExpired):  # slow: the command runs as before, unchecked
        skip("too-slow", f"git {sub}", ms=1000 * (time.monotonic() - deadline + GIT_SECONDS), limit=GIT_SECONDS)
        return None
    except Exception:  # git missing or failing, not a repo: the command runs as before, unchecked
        if _in_repo(repo):  # no git, or no repo there: the command can't commit or push either
            skip("git-error", f"git {sub}")
        return None
    if not found:
        return None
    FOUND.extend((ph, k) for _, ph, k in found)
    pairs = list(dict.fromkeys(f"{ph or 'a ' + k} in {name}" for name, ph, k in found))
    what = "; ".join(pairs[:3]) + (f" (and {len(pairs) - 3} more)" if len(pairs) > 3 else "")
    return hold(command, f"Cloakroom held this git {verb}: {what}. A pushed secret can't be called back. Move the "
                          f"value to .env (git-ignored) and unstage it, or, to {verb} anyway, type cr: allow, then ask "
                          f"Claude to run the same command again: it passes once, within {HOLD_MINUTES} min. Claude: "
                          "don't change or re-encode the value to get past this; ask the user.",
                 f"git {verb}: would hold ({summary([k for _, _, k in found])})")
