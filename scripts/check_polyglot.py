"""CI check: every hook command works in each shell Claude Code may use, with and without cloakroom on PATH.

With cloakroom: a post hook masks a planted key. Without: pre denies, prompt passes with a warning.
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
names = ("cloakroom", "cloakroom.exe", "cloakroom.cmd")
no_cloakroom_path = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep)
                                    if not any(os.path.exists(os.path.join(p, n)) for n in names))
key = "ghp_" + "R8kL2mQ9vX4nT7bW1cY5zH3jF6dS0aP2eG8u"
failures = []


def run(shell, event, payload, path):
    env = dict(os.environ, PATH=path, CLOAKROOM_MODE="rules", PYTHONUTF8="1")
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
    ok = "decision" not in out and "NOT protecting" in out.get("systemMessage", "")
    print(f"{name:<11} without cloakroom prompt warns:  {ok}")
    failures += [] if ok else [(name, "prompt-missing", out)]

print("shells tested:", [s[0] for s in shells])
if failures:
    print(json.dumps(failures, indent=1))
    sys.exit(1)


