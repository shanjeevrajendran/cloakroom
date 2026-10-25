"""Generate the plugin's hook files: plugin/hooks/hooks.json, plugin/scripts/hook.sh and plugin/scripts/hook.ps1.

    python scripts/gen_hooks.py            # write all three
    python scripts/gen_hooks.py --check    # exit 1 if a committed file differs from what this script writes

hooks.json only launches a fixed script inside the plugin, so the Claude plugin directory can read which program
each hook runs. Claude Code runs hook commands with sh/bash (Git Bash on Windows), or with PowerShell on Windows
without Git Bash, so each command is a short bash/PowerShell polyglot (idea from maisecrets, Apache-2.0:
https://github.com/Mcpgate-de/maisecrets): bash runs `set -- \\`# <#\\`` (the backticks hold only a comment), then
scripts/hook.sh, and exits; PowerShell reads `# <#` ... `#>` as a comment, skips the bash lines, and runs
scripts/hook.ps1 in a second copy of the same PowerShell (pwsh or powershell.exe, so execution policy can be bypassed). Claude Code substitutes ${CLAUDE_PLUGIN_ROOT} as text before either shell sees the command.

The scripts find the cloakroom program on PATH, in ~/.local/bin (where uv and pipx put it) or in ~/.cloakroom/venv,
since those are often missing from the PATH Claude Code sees. If it isn't installed the hook still answers itself:
every tool call is denied before it runs, so no output exists to leak, and prompts pass with a warning so the user
is never locked out. If the machine has something to install with (scripts/bootstrap.sh --check decides), the hook
starts bootstrap.sh (.ps1 on Windows) in the background: rules-only first, then the model. After a failed install
the hook shows numbered steps for the reason in ~/.cloakroom/bootstrap.reason (offline, uv installer checksum
mismatch, nothing to download with, anything else) and retries after 10 minutes instead of on every call.

Upgrades: the scripts carry the plugin's version (PIN, from plugin.json). When ~/.cloakroom/upgrade.stamp holds
anything else, the plugin was updated: the hook starts bootstrap.sh --upgrade (bootstrap.ps1 -Upgrade) in the
background and keeps running the installed program meanwhile. The installer writes the stamp, so every later call
costs one read of a small file. A failed upgrade (stamp failed:PIN) retries after 10 minutes. Steps the user must
take themselves (an older install the installer can't upgrade) wait in ~/.cloakroom/upgrade.notice and are shown
once, at the next session start.
"""
import json
import os
import sys

STOP = "To stop it: turn off the cloakroom plugin in /plugin."
INSTALLING = ("cloakroom: installing itself (one time). Rules-only protection starts in about a minute, the full "
              "model follows in a few minutes. Tool calls are held back until then; progress in "
              "~/.cloakroom/bootstrap.log. " + STOP)
LOG = "Log: ~/.cloakroom/bootstrap.log. Tool calls are held back until then. "
RESTART = "Then restart Claude Code (/exit, then claude)"
# The same steps are written to bootstrap.log by plugin/scripts/bootstrap.sh and bootstrap.ps1 (steps / Write-Steps).
NO_UV = ("cloakroom: not installed, and nothing to install it with (no curl, wget, uv, pipx or Python 3.10+). "
         "To finish: 1. Install curl (Linux: sudo apt install curl on Ubuntu/Debian, sudo dnf install curl on "
         "Fedora/RHEL) or uv (Mac: brew install uv). 2. " + RESTART + " and Cloakroom installs itself. " + LOG + STOP)
FAILED_HEAD = "cloakroom: the automatic install failed; it tries again in 10 minutes. "
FAILED = (FAILED_HEAD + "To finish: 1. Read the error at the end of ~/.cloakroom/bootstrap.log. 2. Fix it, or install "
          "Cloakroom yourself (command: https://github.com/shanjeevrajendran/cloakroom#get-started). 3. " + RESTART +
          ". " + LOG + STOP)
OFFLINE = (FAILED_HEAD + "It could not reach the internet. To finish: 1. Connect to the internet (behind a proxy or "
           "firewall, allow astral.sh, releases.astral.sh, pypi.org, files.pythonhosted.org and huggingface.co). "
           "2. " + RESTART + ". " + LOG + STOP)
CHECKSUM = (FAILED_HEAD + "The uv installer from astral.sh did not match its pinned checksum, so it was not run. "
            "To finish: 1. Install uv yourself (Mac: brew install uv; Windows: winget install astral-sh.uv; Linux: "
            "https://docs.astral.sh/uv/getting-started/installation/, or on Ubuntu/Debian sudo apt install "
            "python3-venv instead). 2. " + RESTART + ". " + LOG + STOP)
MESSAGES = (INSTALLING, NO_UV, FAILED, OFFLINE, CHECKSUM)  # hook variables INST NOINST FAILED OFFLINE CHECKSUM
UPDATING = ("cloakroom: updating itself to the new plugin version (about a minute). Tool calls are held back until "
            "then; progress in ~/.cloakroom/bootstrap.log. " + STOP)
ANSWERS = MESSAGES + (UPDATING,)  # ... and UPDATING
NOANSWER = ("cloakroom: the cloakroom program gave no answer, so this was stopped to keep data private. Run: "
            "cloakroom doctor. " + STOP)  # hook.ps1 only: $noanswer
EVENTS = ("start", "prompt", "pre", "post")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin")
with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as _f:
    PIN = json.load(_f)["version"]  # the release this plugin version installs; bootstrap.sh/.ps1 pin the same
GENERATED = "Generated by scripts/gen_hooks.py in the cloakroom repository; edit that file, not this one."


def fallback(event: str, msg: str) -> dict:
    """The answer when the cloakroom program isn't installed yet: prompts pass with a warning (the user is never
    locked out, decision 2026-10-11), tool calls are denied, so no output exists to leak."""
    deny = "Cloakroom blocked this tool call: " + msg.split(": ", 1)[1]
    return {
        "start": {"systemMessage": msg},
        "prompt": {"systemMessage": msg, "hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                   "additionalContext": "The Cloakroom privacy plugin is enabled but its program is not ready "
                                        "yet (installing or updating), so every tool call is denied until it is. "
                                        "Tell the user what the Cloakroom message says if they ask why tools are "
                                        "blocked."}},
        "pre": {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": deny}},
        # tools never run while cloakroom is missing (pre denies), so there is no real output to withhold here
        "post": {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": f"[{deny}]"}},
    }[event]


def answers(event: str) -> tuple[str, ...]:
    """The MESSAGES answers for one event, as one-line JSON without single quotes."""
    return tuple(json.dumps(fallback(event, m)).replace("'", "") for m in ANSWERS)


def command(event: str) -> str:
    """The hooks.json command: launch scripts/hook.sh (bash) or scripts/hook.ps1 (PowerShell) for this event."""
    return "\n".join([
        "set -- `# <#`",
        f'bash "${{CLAUDE_PLUGIN_ROOT}}/scripts/hook.sh" {event}',
        "exit",
        ": <<'#PS_END'",
        "#>",
        # UTF-8 without a byte-order mark: Windows PowerShell 5.1's [Text.Encoding]::UTF8 adds one to piped input
        "$u = New-Object System.Text.UTF8Encoding $false; [Console]::InputEncoding = $u; "
        "[Console]::OutputEncoding = $u; $OutputEncoding = $u",
        # `powershell` by name: it exists on every Windows, also when Claude Code runs under pwsh 7, and the Claude
        # plugin directory refuses a computed program or a statement that starts with a variable
        "[Console]::In.ReadToEnd() | powershell -NoProfile -ExecutionPolicy Bypass -File "
        f'"${{CLAUDE_PLUGIN_ROOT}}/scripts/hook.ps1" {event}; exit $LASTEXITCODE',
        "#PS_END",
    ])


def hooks() -> dict:
    def entry(event, timeout, matcher=None):
        e = {"hooks": [{"type": "command", "command": command(event), "timeout": timeout}]}
        if matcher:
            e = {"matcher": matcher, **e}
        return [e]
    return {"hooks": {"SessionStart": entry("start", 30), "UserPromptSubmit": entry("prompt", 60),
                      "PreToolUse": entry("pre", 60, "*"), "PostToolUse": entry("post", 120, "*")}}


def hook_sh() -> str:
    cases = "".join(f"  {e}) INST='{i}'\n    NOINST='{n}'\n    FAILED='{f}'\n    OFFLINE='{o}'\n    CHECKSUM='{c}'\n"
                    f"    UPDATING='{u}' ;;\n"
                    for e in EVENTS for i, n, f, o, c, u in [answers(e)])
    return f"""#!/usr/bin/env bash
# Cloakroom hook launcher for bash (macOS, Linux, Git Bash on Windows): hook.sh start|prompt|pre|post, hook JSON on
# stdin. Runs the cloakroom program if it is installed; otherwise answers for it and starts the installer.
# {GENERATED}
E="${{1:-}}"
case "$E" in
{cases}  *) echo "usage: hook.sh start|prompt|pre|post" >&2; exit 2 ;;
esac
H="${{CLOAKROOM_HOME:-$HOME/.cloakroom}}"
PIN={PIN}
# Git Bash on Windows (W=1): .exe programs, and the PowerShell installer (bootstrap.ps1), which builds the same
# Windows layout the PowerShell hook looks in
W=; case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) W=1 ;; esac
# the program: on PATH, in ~/.local/bin (where the installers put it) or in ~/.cloakroom/venv
CR="$(command -v cloakroom 2>/dev/null)"
for C in "$HOME/.local/bin/cloakroom" "$HOME/.local/bin/cloakroom.exe" "$H/venv/bin/cloakroom" "$H/venv/Scripts/cloakroom.exe"; do
  [ -z "$CR" ] && [ -x "$C" ] && CR="$C"
done
# an upgrade is swapping the program's files: answer for it, never run half-replaced code (a hold older than 30
# minutes is stale)
if [ -e "$H/bootstrap.lock/upgrade" ] && [ -z "$(find "$H/bootstrap.lock/upgrade" -mmin +30 2>/dev/null)" ]; then
  printf '%s\\n' "$UPDATING"
elif [ -n "$CR" ]; then
  # the plugin was updated: upgrade in the background, keep running the installed program meanwhile
  S=; [ -f "$H/upgrade.stamp" ] && read -r S <"$H/upgrade.stamp"
  if [ "$S" != "$PIN" ] && [ ! -d "$H/bootstrap.lock" ] &&
     {{ [ "$S" != "failed:$PIN" ] || [ -n "$(find "$H/upgrade.stamp" -mmin +10 2>/dev/null)" ]; }}; then
    if [ -n "$W" ] && command -v powershell >/dev/null 2>&1; then
      (nohup powershell -NoProfile -ExecutionPolicy Bypass -File "${{CLAUDE_PLUGIN_ROOT}}/scripts/bootstrap.ps1" -Upgrade </dev/null >/dev/null 2>&1 &)
    else
      (nohup bash "${{CLAUDE_PLUGIN_ROOT}}/scripts/bootstrap.sh" --upgrade </dev/null >/dev/null 2>&1 &)
    fi
  fi
  # steps the user must take to upgrade (left by the installer): once, at a session start
  if [ "$E" = start ] && [ -f "$H/upgrade.notice" ]; then
    "$CR" hook "$E" >/dev/null; cat "$H/upgrade.notice"; rm -f "$H/upgrade.notice"
  else
    "$CR" hook "$E"
  fi
# a failed install retries after 10 minutes, not on every call; the installer leaves the reason for the steps
elif [ "$(cat "$H/bootstrap.state" 2>/dev/null)" = failed ] && [ -z "$(find "$H/bootstrap.state" -mmin +10 2>/dev/null)" ]; then
  case "$(cat "$H/bootstrap.reason" 2>/dev/null)" in
    offline) printf '%s\\n' "$OFFLINE" ;;
    checksum) printf '%s\\n' "$CHECKSUM" ;;
    nofetch) printf '%s\\n' "$NOINST" ;;
    *) printf '%s\\n' "$FAILED" ;;
  esac
elif [ -n "$W" ] && command -v powershell >/dev/null 2>&1; then
  (nohup powershell -NoProfile -ExecutionPolicy Bypass -File "${{CLAUDE_PLUGIN_ROOT}}/scripts/bootstrap.ps1" </dev/null >/dev/null 2>&1 &)
  printf '%s\\n' "$INST"
elif bash "${{CLAUDE_PLUGIN_ROOT}}/scripts/bootstrap.sh" --check; then
  (nohup bash "${{CLAUDE_PLUGIN_ROOT}}/scripts/bootstrap.sh" </dev/null >/dev/null 2>&1 &)
  printf '%s\\n' "$INST"
else
  printf '%s\\n' "$NOINST"
fi
exit 0
"""


def hook_ps1() -> str:
    q = lambda s: s.replace("'", "''")  # noqa: E731  PowerShell single-quoted string
    cases = "".join(f"  '{e}' {{ $inst = '{q(i)}'\n    $noinst = '{q(n)}'\n    $failed = '{q(f)}'\n"
                    f"    $offline = '{q(o)}'\n    $checksum = '{q(c)}'\n    $updating = '{q(u)}'\n"
                    f"    $noanswer = '{q(json.dumps(fallback(e, NOANSWER)))}' }}\n"
                    for e in EVENTS for i, n, f, o, c, u in [answers(e)])
    return f"""# Cloakroom hook launcher for PowerShell (Windows without Git Bash): hook.ps1 start|prompt|pre|post, hook JSON on
# stdin. Runs the cloakroom program if it is installed; otherwise answers for it and starts the installer.
# {GENERATED}
param([string]$E)
# UTF-8 without a byte-order mark: Windows PowerShell 5.1's [Text.Encoding]::UTF8 adds one to piped input
$u = New-Object System.Text.UTF8Encoding $false; [Console]::InputEncoding = $u; [Console]::OutputEncoding = $u; $OutputEncoding = $u
switch ($E) {{
{cases}  default {{ [Console]::Error.WriteLine('usage: hook.ps1 start|prompt|pre|post'); exit 2 }}
}}
$h = if ($env:CLOAKROOM_HOME) {{ $env:CLOAKROOM_HOME }} else {{ Join-Path $env:USERPROFILE '.cloakroom' }}
$pin = '{PIN}'
# the program: on PATH, in ~/.local/bin (where the installers put it) or in ~/.cloakroom/venv
$cr = (Get-Command cloakroom -ErrorAction SilentlyContinue).Source
foreach ($c in @((Join-Path $env:USERPROFILE '.local\\bin\\cloakroom.exe'), (Join-Path $h 'venv\\Scripts\\cloakroom.exe'))) {{
  if (-not $cr -and (Test-Path $c)) {{ $cr = $c }}
}}
# a failed install retries after 10 minutes, not on every call
$st = Join-Path $h 'bootstrap.state'
$recent = (Test-Path $st) -and ((Get-Content $st -ErrorAction SilentlyContinue) -eq 'failed') -and
  (((Get-Date) - (Get-Item $st).LastWriteTime).TotalMinutes -lt 10)
$boot = Join-Path $env:CLAUDE_PLUGIN_ROOT 'scripts\\bootstrap.ps1'
$bargs = '-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $boot + '"')
$ps = if ($PSVersionTable.PSEdition -eq 'Core') {{ 'pwsh' }} else {{ 'powershell' }}
# an upgrade is swapping the program's files (Windows can't replace files in use): answer for it, never start the
# old program (a hold older than 30 minutes is stale)
$hold = Join-Path $h 'bootstrap.lock\\upgrade'
if ((Test-Path $hold) -and (((Get-Date) - (Get-Item $hold).LastWriteTime).TotalMinutes -lt 30)) {{ $updating }}
elseif ($cr) {{
  # the plugin was updated: upgrade in the background, keep running the installed program meanwhile
  $sf = Join-Path $h 'upgrade.stamp'
  $s = if (Test-Path $sf) {{ (Get-Content $sf -TotalCount 1 -ErrorAction SilentlyContinue) -join '' }} else {{ '' }}
  if ($s -ne $pin -and -not (Test-Path (Join-Path $h 'bootstrap.lock')) -and
      ($s -ne "failed:$pin" -or ((Get-Date) - (Get-Item $sf).LastWriteTime).TotalMinutes -ge 10)) {{
    Start-Process $ps -WindowStyle Hidden -ArgumentList ($bargs + '-Upgrade') | Out-Null
  }}
  # steps the user must take to upgrade (left by the installer): once, at a session start
  $nf = Join-Path $h 'upgrade.notice'
  if ($E -eq 'start' -and (Test-Path $nf)) {{
    [Console]::In.ReadToEnd() | & $cr hook $E | Out-Null; Get-Content $nf -Raw; Remove-Item -Force $nf
  }}
  else {{
    # a silent or garbled program must not let a tool's output through: fail closed. A program at the PIN always
    # answers (at least {{}}); an older one still waiting for its upgrade answers nothing for a clean call.
    $o = ([Console]::In.ReadToEnd() | & $cr hook $E | Out-String).Trim()
    $ok = $false; if ($o) {{ try {{ $null = ConvertFrom-Json $o -ErrorAction Stop; $ok = $true }} catch {{ }} }}
    if ($ok -or $E -eq 'start') {{ $o }} elseif ($o -or $s -eq $pin) {{ $noanswer }}
  }}
}}
elseif ($recent) {{  # the installer leaves the reason for the steps
  switch ((Get-Content (Join-Path $h 'bootstrap.reason') -ErrorAction SilentlyContinue) -join '') {{
    'offline' {{ $offline }}
    'checksum' {{ $checksum }}
    default {{ $failed }}
  }}
}}
else {{
  & $boot -Check
  if ($LASTEXITCODE -eq 0) {{
    Start-Process $ps -WindowStyle Hidden -ArgumentList $bargs | Out-Null
    $inst
  }}
  else {{ $noinst }}
}}
exit 0
"""


def outputs() -> dict:
    return {os.path.join(ROOT, "hooks", "hooks.json"): json.dumps(hooks(), indent=2) + "\n",
            os.path.join(ROOT, "scripts", "hook.sh"): hook_sh(),
            os.path.join(ROOT, "scripts", "hook.ps1"): hook_ps1()}


if __name__ == "__main__":
    stale = []
    for path, text in outputs().items():
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if "--check" in sys.argv:
            stale += [] if old == text else [os.path.relpath(path)]
        elif old != text:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
    if "--check" not in sys.argv:
        os.chmod(os.path.join(ROOT, "scripts", "hook.sh"), 0o755)
    if stale:
        sys.exit("out of date (run python scripts/gen_hooks.py): " + ", ".join(stale))
