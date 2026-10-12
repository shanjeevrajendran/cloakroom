"""Generate plugin/hooks/hooks.json.

Each hook command is one string that both bash and PowerShell can run (the idea comes from maisecrets,
Apache-2.0: https://github.com/Mcpgate-de/maisecrets). Claude Code runs hooks with bash, or with PowerShell on
Windows without Git Bash. If `cloakroom` isn't installed the command still answers itself: every tool call is denied
before it runs, so no output exists to leak, and prompts pass with a warning so the user is never locked out.
With uv, pipx or Python on the machine it also starts scripts/bootstrap.sh (.ps1 on Windows) in the background,
which installs Cloakroom: rules-only first, then the model. The program is looked for on PATH, in uv's tool folder,
in ~/.local/bin (pipx) and in ~/.cloakroom/venv, since those are often missing from the PATH Claude Code sees.
After a failed install the hook says so and retries after 10 minutes instead of on every call.

How the polyglot works: bash runs `set -- \\`# <#\\`` (the backticks hold only a comment), runs its own block and
exits; the PowerShell block below sits in a heredoc bash never reaches. PowerShell reads `# <#` ... `#>` as a
comment, skips the bash block, and runs its own.

    python scripts/gen_hooks.py > plugin/hooks/hooks.json
"""
import json

STOP = "To stop it: turn off the cloakroom plugin in /plugin."
INSTALLING = ("cloakroom: installing itself (one time). Rules-only protection starts in about a minute, the full "
              "model follows in a few minutes. Tool calls are held back until then; progress in "
              "~/.cloakroom/bootstrap.log. " + STOP)
NO_UV = ("cloakroom: not installed, and nothing to install it with (uv, pipx or Python 3.10+). Install uv: "
         "https://docs.astral.sh/uv/getting-started/installation/, "
         "then restart Claude Code and Cloakroom installs itself. Tool calls are held back until then. " + STOP)
FAILED = ("cloakroom: the automatic install failed (details: ~/.cloakroom/bootstrap.log); it tries again in 10 minutes. "
          "On Ubuntu/Debian it usually needs: sudo apt install python3-venv. Or install it yourself, see "
          "https://github.com/shanjeevrajendran/cloakroom#get-started for the command. Tool calls are held back until "
          "then. " + STOP)


def fallback(event: str, msg: str) -> dict:
    """The answer when the cloakroom program isn't installed yet: prompts pass with a warning (the user is never
    locked out, decision 2026-10-11), tool calls are denied, so no output exists to leak."""
    deny = "Cloakroom blocked this tool call: " + msg.split(": ", 1)[1]
    return {
        "start": {"systemMessage": msg},
        "prompt": {"systemMessage": msg, "hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                   "additionalContext": "The Cloakroom privacy plugin is enabled but its program is not installed "
                                        "yet, so every tool call is denied until it is. Tell the user what the "
                                        "Cloakroom message says if they ask why tools are blocked."}},
        "pre": {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": deny}},
        # tools never run while cloakroom is missing (pre denies), so there is no real output to withhold here
        "post": {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": f"[{deny}]"}},
    }[event]


def command(event: str) -> str:
    inst = json.dumps(fallback(event, INSTALLING)).replace("'", "")
    nouv = json.dumps(fallback(event, NO_UV)).replace("'", "")
    failed = json.dumps(fallback(event, FAILED)).replace("'", "")
    return "\n".join([
        "set -- `# <#`",
        # find cloakroom on PATH, in uv's tool folder, ~/.local/bin (pipx) or ~/.cloakroom/venv
        # Git Bash on Windows (W=1): .exe programs in venv/Scripts, python or py instead of python3, and the
        # PowerShell installer (bootstrap.ps1), which builds the same Windows layout PowerShell hooks look in
        'H="${CLOAKROOM_HOME:-$HOME/.cloakroom}"; W=; case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) W=1;; esac; '
        'CR="$(command -v cloakroom 2>/dev/null)"; '
        'if [ -z "$CR" ] && command -v uv >/dev/null 2>&1; then B="$(uv tool dir --bin 2>/dev/null)"; '
        'for C in "$B/cloakroom" "$B/cloakroom.exe"; do [ -z "$CR" ] && [ -x "$C" ] && CR="$C"; done; fi; '
        'for C in "$HOME/.local/bin/cloakroom" "$HOME/.local/bin/cloakroom.exe" "$H/venv/bin/cloakroom" '
        '"$H/venv/Scripts/cloakroom.exe"; do [ -z "$CR" ] && [ -x "$C" ] && CR="$C"; done; '
        f'if [ -n "$CR" ]; then "$CR" hook {event}; '
        # a failed install retries after 10 minutes, not on every call
        'elif [ "$(cat "$H/bootstrap.state" 2>/dev/null)" = failed ] && [ -z "$(find "$H/bootstrap.state" -mmin +10 2>/dev/null)" ]; then '
        f"printf '%s\\n' '{failed}'; "
        'elif [ -n "$W" ] && command -v powershell >/dev/null 2>&1; then '
        '(nohup powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap.ps1" '
        '</dev/null >/dev/null 2>&1 &); '
        f"printf '%s\\n' '{inst}'; "
        'elif command -v uv >/dev/null 2>&1 || command -v pipx >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1; then '
        '(nohup bash "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap.sh" </dev/null >/dev/null 2>&1 &); '
        f"printf '%s\\n' '{inst}'; "
        f"else printf '%s\\n' '{nouv}'; fi; exit 0",
        ": <<'#PS_END'",
        "#>",
        # UTF-8 without a byte-order mark: Windows PowerShell 5.1's [Text.Encoding]::UTF8 adds one to piped input
        "$u = New-Object System.Text.UTF8Encoding $false; [Console]::InputEncoding = $u; "
        "[Console]::OutputEncoding = $u; $OutputEncoding = $u; "
        "$h = if ($env:CLOAKROOM_HOME) { $env:CLOAKROOM_HOME } else { Join-Path $env:USERPROFILE '.cloakroom' }; "
        "$cr = (Get-Command cloakroom -ErrorAction SilentlyContinue).Source; "
        "$uv = Get-Command uv -ErrorAction SilentlyContinue; "
        "if (-not $cr -and $uv) { $b = (& uv tool dir --bin 2>$null); "
        "if ($b -and (Test-Path (Join-Path $b 'cloakroom.exe'))) { $cr = Join-Path $b 'cloakroom.exe' } }; "
        "foreach ($c in @((Join-Path $env:USERPROFILE '.local\\bin\\cloakroom.exe'), (Join-Path $h 'venv\\Scripts\\cloakroom.exe'))) "
        "{ if (-not $cr -and (Test-Path $c)) { $cr = $c } }; "
        "$st = Join-Path $h 'bootstrap.state'; "
        "$recent = (Test-Path $st) -and ((Get-Content $st -ErrorAction SilentlyContinue) -eq 'failed') -and "
        "(((Get-Date) - (Get-Item $st).LastWriteTime).TotalMinutes -lt 10); "
        "$can = $uv -or (Get-Command pipx -ErrorAction SilentlyContinue) -or (Get-Command py -ErrorAction SilentlyContinue) "
        "-or (Get-Command python -ErrorAction SilentlyContinue); "
        f"if ($cr) {{ [Console]::In.ReadToEnd() | & $cr hook {event} }} "
        f"elseif ($recent) {{ '{failed.replace(chr(39), chr(39)*2)}' }} "
        "elseif ($can) { Start-Process (Get-Process -Id $PID).Path -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"
        "(Join-Path $env:CLAUDE_PLUGIN_ROOT 'scripts\\bootstrap.ps1') | Out-Null; "
        f"'{inst.replace(chr(39), chr(39)*2)}' }} "
        f"else {{ '{nouv.replace(chr(39), chr(39)*2)}' }}; exit 0",
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


if __name__ == "__main__":
    print(json.dumps(hooks(), indent=2))
