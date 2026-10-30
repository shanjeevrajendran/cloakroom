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


NON_TEXT_READ = {"pdf", "parts", "image"}  # Read results whose content isn't text


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


def _mcp_withhold(raw, msg):
    """Fail-closed MCP result: every content block becomes a text block (an image block holds the reason, never
    pixels or text in its data field), other strings are blanked, wrappers and the JSON-string form keep their shape."""
    placed = [False]

    def text():
        t = "" if placed[0] else msg
        placed[0] = True
        return t

    def walk(v):
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            kind = v.get("type")
            if kind == "text":
                return {"type": "text", "text": text()}
            if isinstance(kind, str):  # image, resource, image_url...: a text block with the reason
                placed[0] = True
                return {"type": "text", "text": msg}
            return {k: x if k in STRUCTURAL or not isinstance(x, (str, dict, list)) else walk(x) for k, x in v.items()}
        return text() if isinstance(v, str) else v

    if isinstance(raw, str) and raw.lstrip()[:1] in ("[", "{"):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return msg
        return json.dumps(walk(parsed)) if isinstance(parsed, (dict, list)) else msg
    if raw is None:
        return [{"type": "text", "text": msg}]
    return walk(raw)


def prompt_warning(why):
    return {"systemMessage": f"cloakroom: this message was NOT fully checked ({why}). Tool calls stay blocked "
                             "until Cloakroom works again. Run: cloakroom doctor"}


def closed(event, why, data):
    msg = f"Cloakroom couldn't check this ({why}), so it was stopped to keep data private."
    if event == "scrub":  # Stop and SessionEnd have nothing to deny or withhold
        return {}
    if event == "start":
        return {"systemMessage": f"cloakroom: not working ({why}); tool output will be withheld until it is fixed. Run: cloakroom doctor"}
    if event == "prompt":
        # Never lock the user out of Claude Code (decision 2026-10-11): the prompt goes through with a warning.
        # hook.handle() first runs the rules in-process, so a pasted key is still blocked when it can be checked.
        return prompt_warning(why)
    if event == "post":
        raw = data.get("tool_response") if isinstance(data, dict) else None
        if isinstance(raw, dict) and raw.get("type") in NON_TEXT_READ:
            # Claude Code ignores a pdf/image result with its data blanked and sends the original: switch to Read's
            # text shape instead (media.py has the same rule)
            path = (raw.get("file") or {}).get("filePath") or (data.get("tool_input") or {}).get("file_path", "")
            raw = {"type": "text", "file": {"filePath": path, "content": f"[{msg}]", "numLines": 1, "startLine": 1,
                                            "totalLines": 1}}
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": raw}}
        if isinstance(data, dict) and str(data.get("tool_name", "")).startswith("mcp__"):
            # MCP: text blocks only, in both fields (updatedMCPToolOutput for Claude Code before updatedToolOutput)
            new = _mcp_withhold(raw, f"[{msg}]")
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": new,
                                           "updatedMCPToolOutput": new}}
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": _withhold(raw, f"[{msg}]")}}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": msg}}


def run(event: str, agent: str = "claude") -> int:
    def answer(out):  # another agent's JSON (agents.py, standard library only); Claude Code's is hook.py's own
        if agent == "claude":
            return out
        from .agents import answer
        return answer(agent, event, out, data)
    data = {}
    raw = sys.stdin.buffer.read()
    try:
        data = json.loads(raw.decode("utf-8-sig"))  # Windows PowerShell 5.1 may prepend a byte-order mark
        if not isinstance(data, dict):
            raise ValueError("hook input is not an object")
    except Exception:
        # Unreadable input must never mean "nothing to do": for a tool result that would pass the output through.
        sys.stdout.write(json.dumps(answer(closed(event, "unreadable hook input", {}))) + "\n")
        return 0
    try:
        from . import hook
        if agent != "claude":  # another agent's input as Claude Code's (agents.py)
            from .agents import request
            data = request(agent, event, data)
        data["_cloakroom_agent"] = agent  # the event log's agent field (cloakroom exposure)
        out = hook.handle(event, data)
    except BaseException as e:  # noqa: BLE001 - fail closed on anything, including import errors
        out = closed(event, type(e).__name__, data)
    try:
        out = answer(out)
    except Exception as e:  # noqa: BLE001 - a translation bug must not mean "nothing to do"
        out = answer(closed(event, type(e).__name__, data))
    # always an answer, {} for a clean pass: the launchers (hook.sh, hook.ps1) treat a silent program as broken and
    # fail closed. Codex and Gemini CLI read {} as no decision too.
    sys.stdout.write(json.dumps(out or {}) + "\n")
    sys.stdout.flush()
    return 0
