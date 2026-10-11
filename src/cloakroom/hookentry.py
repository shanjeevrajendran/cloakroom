"""Crash-proof entry for Claude Code hooks: `cloakroom hook <event>`.

Imports only the standard library up front. If anything fails later, even importing Cloakroom itself, it still
answers block / withhold / deny: Claude Code lets a tool run when a hook exits with an error.
"""
import json
import sys


# Fields Claude Code needs intact to accept a replacement result; changing them makes it ignore the replacement
# and pass the original output through.
STRUCTURAL = {"type", "filePath", "file_path", "mode", "kind", "status", "encoding", "language", "isImage",
              "interrupted", "noOutputExpected", "returnCodeInterpretation"}


def _withhold(raw, msg):
    """Replace every content string with msg (first) or "" (rest), keeping structural fields and the shape."""
    if isinstance(raw, dict):
        out, placed = {}, False
        for k, v in raw.items():
            if k in STRUCTURAL or not isinstance(v, (str, dict, list)):
                out[k] = v
                continue
            out[k] = _withhold(v, "" if placed else msg)
            placed = placed or isinstance(v, str) or bool(v)
        return out
    if isinstance(raw, list):
        return [_withhold(v, msg if i == 0 else "") for i, v in enumerate(raw)]
    return msg


def prompt_warning(why):
    return {"systemMessage": f"cloakroom: this message was NOT fully checked ({why}). Tool calls stay blocked "
                             "until Cloakroom works again. Run: cloakroom doctor"}


def closed(event, why, data):
    msg = f"Cloakroom couldn't check this ({why}), so it was stopped to keep data private."
    if event == "start":
        return {"systemMessage": f"cloakroom: not working ({why}); tool output will be withheld until it is fixed. Run: cloakroom doctor"}
    if event == "prompt":
        # Never lock the user out of Claude Code (decision 2026-10-11): the prompt goes through with a warning.
        # hook.handle() first runs the rules in-process, so a pasted key is still blocked when it can be checked.
        return prompt_warning(why)
    if event == "post":
        raw = data.get("tool_response") if isinstance(data, dict) else None
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": _withhold(raw, f"[{msg}]")}}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": msg}}


def run(event: str) -> int:
    raw = sys.stdin.buffer.read()
    try:
        data = json.loads(raw.decode("utf-8-sig"))  # Windows PowerShell 5.1 may prepend a byte-order mark
        if not isinstance(data, dict):
            raise ValueError("hook input is not an object")
    except Exception:
        # Unreadable input must never mean "nothing to do": for a tool result that would pass the output through.
        sys.stdout.write(json.dumps(closed(event, "unreadable hook input", {})) + "\n")
        return 0
    try:
        from . import hook
        out = hook.handle(event, data)
    except BaseException as e:  # noqa: BLE001 - fail closed on anything, including import errors
        out = closed(event, type(e).__name__, data)
    if out:
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()
    return 0
