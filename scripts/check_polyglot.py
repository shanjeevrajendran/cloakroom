"""CI check: every hook command works in each shell Claude Code may use, with and without cloakroom on PATH.

With cloakroom: a post hook masks a planted key. Without: pre denies, prompt passes with a warning. Without
cloakroom but with uv (a stub here): the first-start installer is launched.
    python scripts/check_polyglot.py
"""
import json
import os
import shutil
import subprocess
import sys

hooks = json.load(open(os.path.join(os.path.dirname(__file__), "..", "plugin", "hooks", "hooks.json")))["hooks"]
cmd = {ev: hooks[name][0]["hooks"][0]["command"] for ev, name in
       [("pre", "PreToolUse"), ("post", "PostToolUse"), ("prompt", "UserPromptSubmit"), ("start", "SessionStart")]}
# Claude Code on Windows uses Git Bash, not the WSL launcher in System32 that `bash` may resolve to.
git_bash = r"C:\Program Files\Git\bin\bash.exe"
bash = git_bash if os.name == "nt" and os.path.exists(git_bash) else "bash"
shells = [s for s in ([bash, "-c"], ["powershell", "-NoProfile", "-Command"], ["pwsh", "-NoProfile", "-Command"])
          if shutil.which(s[0]) or os.path.exists(s[0])]
# also hide uv, so the "without cloakroom" runs can never start a real install
names = ("cloakroom", "cloakroom.exe", "cloakroom.cmd", "uv", "uv.exe")
no_cloakroom_path = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep)
                                    if not any(os.path.exists(os.path.join(p, n)) for n in names))
key = "ghp_" + "R8kL2mQ9vX4nT7bW1cY5zH3jF6dS0aP2eG8u"
failures = []


import tempfile
# "without cloakroom" runs also get an empty home: the hook looks in ~/.local/bin and ~/.cloakroom/venv too
empty_home = tempfile.mkdtemp()


def run(shell, event, payload, path):
    env = dict(os.environ, PATH=path, CLOAKROOM_MODE="rules", PYTHONUTF8="1")
    if path != os.environ["PATH"]:
        env.update(HOME=empty_home, USERPROFILE=empty_home, CLOAKROOM_HOME=os.path.join(empty_home, ".cloakroom"))
    p = subprocess.run(shell + [cmd[event]], input=json.dumps(payload), capture_output=True, text=True,
                       encoding="utf-8", env=env, timeout=120)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"_raw": p.stdout[-300:], "_err": p.stderr[-300:], "_code": p.returncode}


for sh in shells:
    name = "git-bash" if sh[0] == git_bash else os.path.basename(sh[0])
    out = run(sh, "post", {"session_id": "ci", "tool_name": "Bash", "tool_response": {"stdout": f"key {key}"}}, os.environ["PATH"])
    ok = key not in json.dumps(out) and "PII_SECRET_1" in json.dumps(out)
    print(f"{name:<11} with cloakroom    post masks key: {ok}")
    failures += [] if ok else [(name, "post", out)]
    out = run(sh, "pre", {"tool_name": "Read", "tool_input": {"file_path": "x"}}, no_cloakroom_path)
    ok = out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    print(f"{name:<11} without cloakroom pre denies:    {ok}")
    failures += [] if ok else [(name, "pre-missing", out)]
    out = run(sh, "prompt", {"prompt": "hi"}, no_cloakroom_path)
    ok = "decision" not in out and "cloakroom" in out.get("systemMessage", "")
    print(f"{name:<11} without cloakroom prompt warns:  {ok}")
    failures += [] if ok else [(name, "prompt-missing", out)]

# Without cloakroom but with uv: the hook starts the installer (bash; a stub uv records the call).
stub = tempfile.mkdtemp()
mark = os.path.join(stub, "installed")
with open(os.path.join(stub, "uv"), "w") as f:
    f.write(f'#!/bin/sh\ncase "$1 $2" in "tool dir") echo {stub}/nobin ;; "tool install") echo "$@" >> {mark} ;; esac\n')
os.chmod(os.path.join(stub, "uv"), 0o755)
if os.name != "nt":
    env_path = stub + os.pathsep + no_cloakroom_path
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin")
    os.environ.update(CLAUDE_PLUGIN_ROOT=root, CLOAKROOM_HOME=os.path.join(stub, "home"))
    out = run([bash, "-c"], "start", {"session_id": "ci"}, env_path)
    import time
    for _ in range(40):
        if os.path.exists(mark):
            break
        time.sleep(0.25)
    ok = "installing itself" in out.get("systemMessage", "") and os.path.exists(mark)
    print(f"{'bash':<11} uv, no cloakroom  installer starts: {ok}")
    failures += [] if ok else [("bash", "bootstrap", out)]

print("shells tested:", [s[0] for s in shells])
if failures:
    print(json.dumps(failures, indent=1))
    sys.exit(1)


