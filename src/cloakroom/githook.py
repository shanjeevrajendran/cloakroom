"""cloakroom install git-hook: the commit check for commits you make yourself, with no agent running.

Adds pre-commit and pre-push hooks to the current repo (core.hooksPath respected). They run `cloakroom git-check`,
which scans the added lines with the agent commit hold's code (hook.git_added): the rules and your always-mask
entries, keys and passwords only. A hook file that isn't Cloakroom's is never changed: install skips it and says how
to chain it by hand. Skip the check once with git's own --no-verify.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time

from . import config

MARK = "# cloakroom git hook"
SECONDS = 10.0  # your own commit can wait longer than an agent's tool call (hook.GIT_SECONDS)
HOOKS = {"pre-commit": "commit", "pre-push": "push"}
ZERO = "0" * 40


def _script(program: str, kind: str) -> str:
    verb = "git commit" if kind == "commit" else "git push"
    return (f"#!/bin/sh\n{MARK}: checks the keys and passwords this {kind} adds (cloakroom install git-hook).\n"
            f"# Skip it once: {verb} --no-verify. Remove it: cloakroom uninstall git-hook\n"
            f"p={shlex.quote(program)}\n"
            '[ -x "$p" ] || p=$(command -v cloakroom) || {\n'
            f'  echo "cloakroom: not installed, so this {kind} was not checked" >&2; exit 0; }}\n'
            f'exec "$p" git-check {kind}\n')


def hooks_dir(cwd: str = ".") -> str:
    """The folder git runs this repo's hooks from: core.hooksPath when set, else .git/hooks (shared by worktrees)."""
    top = _git(cwd, "rev-parse", "--show-toplevel")
    return os.path.normpath(os.path.join(top, os.path.expanduser(_git(top, "rev-parse", "--git-path", "hooks"))))


def _git(cwd: str, *argv) -> str:
    r = subprocess.run(["git", "-C", cwd, *argv], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise ValueError(r.stderr.strip() or "git failed")
    return r.stdout.strip()


def _ours(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return MARK in f.read(4096)
    except OSError:
        return False


def install(remove: bool = False, cwd: str = ".") -> int:
    """cloakroom install|uninstall git-hook, for the repo cwd is in. Safe to run twice."""
    try:
        d = hooks_dir(cwd)
    except (ValueError, OSError) as e:
        print(f"cloakroom: not in a git repo ({e})", file=sys.stderr)
        return 1
    if not remove:
        from .agents import _program
        program = _program()
        if not program:
            print("cloakroom: can't find the cloakroom program for the hook to run; install it first "
                  "(uv tool install 'cloakroom[model,torch]')", file=sys.stderr)
            return 1
        os.makedirs(d, exist_ok=True)
    skipped = 0
    for name, kind in HOOKS.items():
        path = os.path.join(d, name)
        exists = os.path.lexists(path)
        if exists and not _ours(path):
            if not remove:
                skipped += 1
                print(f"cloakroom: {path} is another tool's hook, left as it is. To add the check, put this line "
                      f"near its top: cloakroom git-check {kind} || exit 1")
            continue
        if remove:
            if exists:
                os.remove(path)
                print(f"cloakroom: removed {path}")
            continue
        tmp = path + ".cloakroom-tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(_script(program, kind))
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)
        print(f"cloakroom: {'updated' if exists else 'added'} {path}")
    if not remove:
        print("Commits and pushes in this repo are now checked for keys and passwords, with the same rules as the "
              "agent hold and your own always-mask values. Skip once: --no-verify. Remove: cloakroom uninstall git-hook")
    return 1 if skipped else 0


def check(kind: str, stdin=None) -> int:
    """The hook itself: exit 1 (git stops) when the commit or push adds a key or password, else 0. Fails open, with a
    line on stderr, when it can't check (git error, too large, too slow, bad settings)."""
    from . import hook
    t0 = time.monotonic()
    if kind == "commit":
        argvs = [["diff", "--cached"]]  # git sets GIT_INDEX_FILE for commit -a and commit <paths>: this is what commits
    else:  # pre-push: "<local ref> <local sha> <remote ref> <remote sha>" per ref on stdin
        argvs = [["log", "-p", "--format=", parts[1], "--not", "--remotes"]
                 for parts in (line.split() for line in (stdin or sys.stdin)) if len(parts) == 4 and parts[1] != ZERO]
        if not argvs:
            return 0
    try:
        found = hook.git_added(".", argvs, None, config.load(), t0 + SECONDS)
        why = "too large or too slow to scan" if found is None else ""
    except Exception as e:  # noqa: BLE001 - never stop your own commit because the check broke
        found, why = None, type(e).__name__
    if found is None:
        # TODO(skips): record a "skipped" event here (events.record) once the skips branch lands
        print(f"cloakroom: this {kind} was not checked ({why})", file=sys.stderr)
        return 0
    if not found:
        return 0
    pairs = list(dict.fromkeys(f"a {k} in {name}" for name, _, k in found))
    what = "; ".join(pairs[:3]) + (f" (and {len(pairs) - 3} more)" if len(pairs) > 3 else "")
    fix = "unstage it" if kind == "commit" else "take it out of those commits (git commit --amend, git rebase)"
    print(f"cloakroom: stopped this {kind}: it adds {what}. A pushed secret can't be called back: move the value to "
          f".env (git-ignored) and {fix}. To {kind} anyway: git {kind} --no-verify", file=sys.stderr)
    return 1
