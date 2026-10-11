"""Generate plugin/hooks/hooks.json.

Each hook command is one string that both bash and PowerShell can run (the idea comes from maisecrets,
Apache-2.0: https://github.com/Mcpgate-de/maisecrets). Claude Code runs hooks with bash, or with PowerShell on
Windows without Git Bash. If `cloakroom` isn't installed the command still answers fail-closed itself:
prompts are blocked and every tool call is denied before it runs, so no output exists to leak.

How the polyglot works: bash runs `set -- \\`# <#\\`` (the backticks hold only a comment), runs its own block and
exits; the PowerShell block below sits in a heredoc bash never reaches. PowerShell reads `# <#` ... `#>` as a
comment, skips the bash block, and runs its own.

    python scripts/gen_hooks.py > plugin/hooks/hooks.json
"""
import json

MISSING = "Cloakroom is not installed or not on PATH, so this was stopped to keep data private. Fix: run 'cloakroom doctor'."
FALLBACK = {
    "start": {"systemMessage": "cloakroom: not installed or not on PATH; every tool call will be denied until it is. "
                               "Install: uv tool install 'cloakroom[model]'"},
    "prompt": {"decision": "block", "reason": MISSING},
    "pre": {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": MISSING}},
    # tools never run while cloakroom is missing (pre denies), so there is no real output to withhold here
    "post": {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": f"[{MISSING}]"}},
}


def command(event: str) -> str:
    fallback = json.dumps(FALLBACK[event]).replace("'", "")
    ps_fallback = fallback.replace("'", "''")
    return "\n".join([
        "set -- `# <#`",
        f"if command -v cloakroom >/dev/null 2>&1; then cloakroom hook {event}; "
        f"else printf '%s\\n' '{fallback}'; fi; exit 0",
        ": <<'#PS_END'",
        "#>",
        # UTF-8 without a byte-order mark: Windows PowerShell 5.1's [Text.Encoding]::UTF8 adds one to piped input
        "$u = New-Object System.Text.UTF8Encoding $false; [Console]::InputEncoding = $u; "
        "[Console]::OutputEncoding = $u; $OutputEncoding = $u; "
        "if (Get-Command cloakroom -ErrorAction SilentlyContinue) { "
        f"[Console]::In.ReadToEnd() | & cloakroom hook {event} }} "
        f"else {{ '{ps_fallback}' }}; exit 0",
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
