"""Coding agents other than Claude Code. hook.py speaks Claude Code's hook JSON; this module turns another agent's
hook input into it and hook.py's answers back, and installs the hooks into that agent's config.

Codex (https://developers.openai.com/codex/hooks) sends the same input field names as Claude Code (session_id, cwd,
prompt, tool_name, tool_input, tool_response, tool_use_id), and tool names Bash, apply_patch and mcp__server__tool,
so only the answer needs translating:
  - a tool result can't be replaced field by field, but a PostToolUse "block" replaces the result the model sees with
    its reason: the masked output goes there;
  - a rewritten tool input needs permissionDecision "allow"; "ask" isn't supported, so an ask becomes a deny.

Gemini CLI (https://geminicli.com/docs/hooks/reference, checked against 0.65 and its source) has its own tool names
(run_shell_command, read_file, write_file, replace, mcp_<server>_<tool>...) with Claude Code's argument names, and
its own answers:
  - BeforeAgent (prompt) can only block: decision "deny" keeps the message out of the history. BeforeModel could
    rewrite the prompt's text, but the real prompt would stay in the history, which Gemini also sends to the model
    from places no hook sees (model routing, compression, loop checks), so it isn't used;
  - BeforeTool takes the whole rewritten input in hookSpecificOutput.tool_input, and "ask" is supported;
  - AfterTool replaces the result only through a deny, whose reason the model gets instead (additionalContext is
    dropped then, so the guide goes into the reason).
Cursor (https://cursor.com/docs/hooks, checked against the command-line agent 2026.10.22) can change a tool's input
but never its output, except an MCP tool's. So Cloakroom masks before the model can see anything:
  - a file read is pointed at a masked copy of the file (preToolUse updated_input, cursor.py);
  - a shell command is rewritten to run inside `cloakroom cursor-run`, which prints only masked output;
  - MCP output is replaced after the call (postToolUse updated_mcp_tool_output);
  - everything else is checked in the tool's input, as for Codex. An ask becomes a deny: Cursor doesn't enforce it
    on preToolUse. The command-line agent doesn't run beforeSubmitPrompt, so typed prompts aren't covered there.
Standard library only: hookentry imports this before anything else can fail.
"""
import json
import os
import re
import shlex
import sys

AGENTS = ("claude", "codex", "gemini", "cursor")
NAMES = {"codex": "Codex", "gemini": "Gemini CLI", "cursor": "Cursor"}
HOOK_EVENTS = {"start": "SessionStart", "prompt": "UserPromptSubmit", "pre": "PreToolUse", "post": "PostToolUse"}
TIMEOUTS = {"start": 30, "prompt": 60, "pre": 60, "post": 120}  # seconds, as for Claude Code
FAILED = ("Cloakroom is not installed or not working, so this was stopped to keep data private. Run: cloakroom doctor")
CODEX_GUIDE = ("Cloakroom note: values like PII_EMAIL_1_k7q2ab are placeholders for real data kept on this machine. Use "
               "them verbatim in apply_patch and in local shell commands (cat, grep, sed, python...): Cloakroom puts "
               "the real values back before the command runs and masks them again in the output. Copy placeholders "
               "exactly, tag included: Cloakroom restores only the ones it issued in this session. Don't try to "
               "recover the real values.")
GEMINI_GUIDE = ("Cloakroom note: values like PII_EMAIL_1_k7q2ab are placeholders for real data kept on this machine. "
                "Use them verbatim in write_file, in replace's new_string and in local shell commands (cat, grep, sed, "
                "python...): Cloakroom puts the real values back before the tool runs and masks them again in the "
                "output. replace's old_string must not contain a placeholder (the file holds the real value, so it "
                "won't match): anchor it on nearby text, or rewrite the file with write_file. Copy placeholders "
                "exactly, tag included: Cloakroom restores only the ones it issued in this session. Don't try to "
                "recover the real values.")
GEMINI_EVENTS = {"start": "SessionStart", "prompt": "BeforeAgent", "pre": "BeforeTool", "post": "AfterTool"}
GEMINI_TOOLS = {"run_shell_command": "Bash", "read_file": "Read", "write_file": "Write", "replace": "Edit",
                "glob": "Glob", "grep_search": "Grep", "list_directory": "LS"}  # same argument names as Claude Code
HELD, NOTE = "_cloakroom_held", "_cloakroom_note"  # set by gemini_request: for gemini_answer, and (NOTE) for hook.py
NOT_CHECKED = ("cloakroom: not working, so this message was not checked. Tool calls stay blocked until it works "
               "again. Run: cloakroom doctor")
ASK_NOTE = " Codex can't ask you from a hook, so the call was stopped; run it yourself if it's what you want."
CURSOR_ASK_NOTE = " Cursor can't ask you from this hook, so the call was stopped; run it yourself if it's what you want."
CURSOR_GUIDE = ("Cloakroom note: values like PII_EMAIL_1_k7q2ab are placeholders for real data kept on this machine. Use "
                "them verbatim in Write and in local shell commands (cat, grep, sed, python...): Cloakroom puts the "
                "real values back before the tool runs and masks them again in the output. When you edit part of a "
                "file, the text you replace must not contain a placeholder (the file holds the real value, so it "
                "won't match): anchor on nearby text, or rewrite the file with Write. Search file contents with a "
                "shell command (rg, grep), not the Grep tool. Copy placeholders exactly, tag included: Cloakroom "
                "restores only the ones it issued in this session. Don't try to recover the real values.")
GREP_DENY = ("Cloakroom: Cursor's Grep tool shows file contents that can't be masked. Search with a shell command "
             "instead (rg or grep): Cloakroom masks its output. Grep that lists only file names (files_with_matches "
             "or count) is fine.")
# Cursor event -> (cloakroom hook event, matcher, failClosed). Tool hooks fail closed; prompt and session hooks never
# lock you out (hookentry.closed).
CURSOR_HOOKS = {
    "sessionStart": ("start", None, False),
    "beforeSubmitPrompt": ("prompt", None, False),
    "preToolUse": ("pre", ".*", True),
    "beforeShellExecution": ("pre", None, True),
    "beforeMCPExecution": ("pre", None, True),
    "beforeReadFile": ("pre", None, True),
    "postToolUse": ("post", ".*", True),
    "postToolUseFailure": ("scrub", ".*", False),
    "sessionEnd": ("scrub", None, False),
}
CURSOR_EVENTS = {"preToolUse": "PreToolUse", "beforeShellExecution": "PreToolUse", "beforeMCPExecution": "PreToolUse",
                 "beforeReadFile": "PreToolUse", "postToolUse": "PostToolUse",
                 "postToolUseFailure": "PostToolUseFailure", "sessionStart": "SessionStart",
                 "sessionEnd": "SessionEnd", "beforeSubmitPrompt": "UserPromptSubmit"}
_PROMPT = re.compile(r"^Cloakroom stopped this prompt: it holds private data \((.+?)\), so it wasn't sent\.")
_COPY = "\nSend this instead:\n"


def _words(text, name):
    """Cloakroom's messages name Claude; on another agent they name that agent."""
    if not isinstance(text, str):
        return text
    return (text.replace("restart Claude Code (/exit, then claude)", f"restart {name}")
            .replace("Claude Code", name).replace("Claude", name))


def _codex_words(text):
    return _words(text, "Codex")


def _prompt_reason(reason, name):
    """hook.py's prompt stop as this agent's: the copy with placeholders kept exactly as it is (never renamed)."""
    m = _PROMPT.match(reason or "")
    if not m:
        return _words(reason, name)
    from .hook import COPY_NOTE, TOO_LONG  # the hook ran, so it can be imported
    head = f"Cloakroom stopped this message: it holds private data ({m.group(1)}), so it wasn't sent."
    cr = "To send it as it is, start the message with cr!"
    if _COPY in reason:  # the copy sits between that line and hook.py's last line (one line, no newline)
        copy = reason.split(_COPY, 1)[1].rpartition("\n")[0]
        return f"{head} {_words(COPY_NOTE, name)}{_COPY}{copy}\n{cr}"
    long = f" {TOO_LONG}" if TOO_LONG in reason else ""
    return (f"{head}{long} To go on, put that detail in a file and ask {name} to read it (Cloakroom masks it on the "
            f"way in), or take it out and send again. {cr}")


def _as_text(value):
    """A replacement tool result as the text the model gets: MCP text blocks joined, anything else as JSON."""
    if isinstance(value, str):
        return value
    blocks = value.get("content") if isinstance(value, dict) else value
    if isinstance(blocks, list) and blocks and all(isinstance(b, dict) and b.get("type") == "text" for b in blocks):
        return "\n".join(str(b.get("text", "")) for b in blocks)
    return json.dumps(value)


def codex_answer(event, out):
    """hook.py's Claude Code answer as Codex's. None stays None (nothing to say: the call goes ahead unchanged)."""
    if not out:
        return out
    out = dict(out)
    for k in ("systemMessage", "stopReason"):
        if k in out:
            out[k] = _codex_words(out[k])
    if out.get("decision") == "block":  # a prompt: say what to do next
        out["reason"] = _prompt_reason(out.get("reason", ""), "Codex")
    h = out.pop("hookSpecificOutput", None)
    if not h:
        return out
    ctx = h.get("additionalContext")
    if ctx:
        from .hook import GUIDE  # only imported once the hook ran, so it can be imported
        ctx = CODEX_GUIDE if ctx == GUIDE else _codex_words(ctx)
    if event == "post":
        if "updatedToolOutput" in h:  # never renamed: this is the tool's own output, masked
            out.update(decision="block", reason=_as_text(h["updatedToolOutput"]) or "[Cloakroom: empty output]")
        h = {"hookEventName": "PostToolUse"}
    elif event == "pre":
        h = {k: v for k, v in h.items() if k in ("hookEventName", "permissionDecision", "permissionDecisionReason",
                                                  "updatedInput")}
        if h.get("permissionDecision") == "ask":  # restored or outbound with placeholders: never run unasked
            h.pop("updatedInput", None)
            h.update(permissionDecision="deny", permissionDecisionReason=h.get("permissionDecisionReason", "") + ASK_NOTE)
        elif "updatedInput" in h:
            h["permissionDecision"] = "allow"
        if "permissionDecisionReason" in h:
            h["permissionDecisionReason"] = _codex_words(h["permissionDecisionReason"])
    else:
        h = {"hookEventName": h.get("hookEventName", HOOK_EVENTS[event])}
    if ctx:
        h["additionalContext"] = ctx
    if len(h) > 1:
        out["hookSpecificOutput"] = h
    return out


def gemini_request(event, data):
    """Gemini's hook input as Claude Code's: tool names, the tool result as text or MCP-style blocks."""
    data = dict(data)
    if _rules_until_warm():
        data[NOTE] = True  # hook.py logs this call as a skipped model check and tells you once per session
    name = str(data.get("tool_name", ""))
    mcp = data.get("mcp_context") if isinstance(data.get("mcp_context"), dict) else {}
    if name.startswith("mcp_") and mcp.get("server_name") and mcp.get("tool_name"):
        name = f"mcp__{mcp['server_name']}__{mcp['tool_name']}"
    if event == "pre":
        data["tool_name"] = GEMINI_TOOLS.get(name, name)
        args = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
        if data["tool_name"] == "Bash" and args.get("dir_path"):  # where the command runs (git checks)
            data["cwd"] = os.path.join(str(data.get("cwd") or os.getcwd()), str(args["dir_path"]))
    elif event == "post":
        resp = data.get("tool_response")
        llm = resp.get("llmContent") if isinstance(resp, dict) else resp  # what the model would get
        data["tool_name"] = name
        if llm is not None and not isinstance(llm, str):
            blocks, held, image = [], 0, False
            for part in llm if isinstance(llm, list) else [llm]:
                inline = part.get("inlineData") if isinstance(part, dict) else None
                if isinstance(part, str) or (isinstance(part, dict) and isinstance(part.get("text"), str)):
                    blocks.append({"type": "text", "text": part if isinstance(part, str) else part["text"]})
                elif isinstance(inline, dict) and str(inline.get("mimeType", "")).startswith("image/"):
                    blocks.append({"type": "image", "data": inline.get("data"), "mimeType": inline["mimeType"]})
                    image = True
                else:  # a PDF, audio, a file reference...: nothing Cloakroom can check, so it never goes as it is
                    kind = (inline or part.get("fileData") or {}).get("mimeType") if isinstance(part, dict) else None
                    blocks.append({"type": "text", "text": f"[Cloakroom held back a {kind or 'non-text'} part from "
                                   "this tool: it can't be checked for secrets or personal data.]"})
                    held += 1
            llm = blocks
            if held:
                data[HELD] = held
            if image and not name.startswith("mcp__"):  # hook.py reads pictures (OCR) or holds them on this path
                data["tool_name"] = f"mcp__gemini__{name}"
        data["tool_response"] = llm
    return data


def _rules_until_warm():
    """Gemini goes on unchecked when a hook runs past its timeout, so a hook never waits for the model to load: until
    the service answers, this call runs on the rules in-process, and the service is started for the next one.
    True when this call runs on the rules."""
    from . import client, config
    cfg = config.load()
    if cfg["mode"] != "full" or client.health(cfg["port"]) is not None:
        return False
    import socket
    try:  # something listening but not answering yet: the service is loading the model
        socket.create_connection(("127.0.0.1", cfg["port"]), 0.5).close()
    except OSError:
        client.start_background()
    os.environ["CLOAKROOM_MODE"] = "rules"  # this process only
    return True


def gemini_answer(event, out, data=None):
    """hook.py's Claude Code answer as Gemini's. None or {} stays as it is: a clean pass."""
    data = data if isinstance(data, dict) else {}
    held = event == "post" and data.get(HELD)
    if not out and not held:
        return out
    out = dict(out or {})
    name = NAMES["gemini"]
    for k in ("systemMessage", "stopReason"):
        if k in out:
            out[k] = _words(out[k], name)
    if out.get("decision") == "block":  # a prompt
        out.update(decision="deny", reason=_prompt_reason(out.get("reason", ""), name))
        if out.get("systemMessage"):  # Gemini shows a stop's systemMessage instead of its reason: keep both
            out["reason"] += "\n\n" + out.pop("systemMessage")
    h = out.pop("hookSpecificOutput", None) or {}
    ctx = h.get("additionalContext")
    if ctx:
        from .hook import GUIDE  # only imported once the hook ran, so it can be imported
        ctx = GEMINI_GUIDE if ctx == GUIDE else _words(ctx, name)
    extra = {}
    if event == "post":
        if "updatedToolOutput" in h or held:  # never renamed: this is the tool's own output, masked
            text = _as_text(h["updatedToolOutput"] if "updatedToolOutput" in h else data.get("tool_response"))
            out.update(decision="deny", reason=(text or "[Cloakroom: empty output]") + (f"\n\n{ctx}" if ctx else ""))
            ctx = None  # Gemini drops additionalContext on a deny
    elif event == "pre":
        ctx = None  # BeforeTool takes none
        reason = _words(h.get("permissionDecisionReason", ""), name)
        if h.get("permissionDecision") == "deny":
            out.update(decision="deny", reason=reason)
        else:
            if "updatedInput" in h:
                extra["tool_input"] = h["updatedInput"]
            if h.get("permissionDecision") == "ask":  # Gemini asks you; the reason is shown with the question
                out["decision"] = "ask"
                out["systemMessage"] = " · ".join(x for x in (reason, out.get("systemMessage")) if x)
    if ctx:
        extra["additionalContext"] = ctx
    if extra:
        out["hookSpecificOutput"] = {"hookEventName": GEMINI_EVENTS[event], **extra}
    return out


def _cursor_words(text):
    """Cloakroom's messages as Cursor's CLI shows them: it has no prompt hook, so cr: allow can't be typed there."""
    return _words(text, "Cursor").replace("type cr: allow", "run cloakroom allow in a terminal") \
        if isinstance(text, str) else text


def _json_or(value, key):
    """MCP input and output arrive as JSON strings: the parsed value, else {key: the string}."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return {key: value}


def cursor_request(event, data):
    """Cursor's hook input as Claude Code's (section 2 of the design). Cursor's own event name stays in
    _cursor_event, so hook.py can tell preToolUse from beforeShellExecution."""
    data = dict(data)
    cursor_event = data.get("hook_event_name")
    data["_cursor_event"] = cursor_event
    data["hook_event_name"] = CURSOR_EVENTS.get(cursor_event, cursor_event)
    args = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    roots = data.get("workspace_roots") if isinstance(data.get("workspace_roots"), list) else []
    data["cwd"] = next((str(c) for c in (data.get("cwd"), args.get("cwd"), roots[0] if roots else None) if c),
                       os.getcwd())
    name = str(data.get("tool_name", ""))
    if cursor_event == "beforeMCPExecution":
        data["tool_name"] = f"mcp__{data.get('mcp_server_name')}__{name}"
        parsed = _json_or(data.get("tool_input"), "input")
        data["tool_input"] = parsed if isinstance(parsed, dict) else {"input": parsed}
    elif cursor_event == "preToolUse":
        if name == "Shell":
            data["tool_name"] = "Bash"
        elif name == "Write" and "content" not in args and ("old_string" in args or "new_string" in args):
            data["tool_name"] = "Edit"
        elif name.startswith("MCP:"):
            data["tool_name"] = "mcp__cursor__" + name[4:]
    elif cursor_event == "postToolUse" and name.startswith("MCP:"):
        data["tool_name"] = "mcp__cursor__" + name[4:]
        data["tool_response"] = _json_or(data.get("tool_output"), "text")
    return data


def cursor_answer(event, out, data=None):
    """hook.py's Claude Code answer as Cursor's (section 2 of the design). Always an object: {} is a clean pass."""
    data = data if isinstance(data, dict) else {}
    h = (out or {}).get("hookSpecificOutput") or {}
    if event == "start":
        return {"additional_context": CURSOR_GUIDE}
    if event == "prompt":
        if (out or {}).get("decision") == "block":
            return {"continue": False, "user_message": _prompt_reason((out or {}).get("reason", ""), "Cursor")}
        return {}  # a broken Cloakroom never blocks a prompt
    if event == "pre":
        reason = _cursor_words(h.get("permissionDecisionReason", ""))
        if h.get("permissionDecision") == "ask":  # Cursor doesn't enforce ask on preToolUse: never run unasked
            reason += CURSOR_ASK_NOTE
        if h.get("permissionDecision") in ("deny", "ask"):
            return {"permission": "deny", "user_message": reason, "agent_message": reason}  # which one reaches the model is untested
        return {"permission": "allow", "updated_input": h["updatedInput"]} if "updatedInput" in h else {}
    if event == "post":
        res = {}
        if "updatedToolOutput" in h and str(data.get("tool_name", "")).startswith(("mcp__", "MCP:")):
            res["updated_mcp_tool_output"] = _as_text(h["updatedToolOutput"]) or "[Cloakroom: empty output]"
        if h.get("additionalContext"):
            from .hook import GUIDE  # only imported once the hook ran, so it can be imported
            ctx = h["additionalContext"]
            res["additional_context"] = CURSOR_GUIDE if ctx == GUIDE else _cursor_words(ctx)
        return res
    return {}  # scrub; systemMessage is dropped: Cursor has no status line, and hook.log keeps it


AGENT = "_cloakroom_agent"  # which agent's hook this is, for hook.py's Codex-only hold


def request(agent, event, data):
    if agent == "codex":
        return {**data, AGENT: agent}
    if agent == "cursor":
        return {**cursor_request(event, data), AGENT: agent}
    return gemini_request(event, data) if agent == "gemini" else data


def answer(agent, event, out, data=None):
    if agent == "codex":
        return codex_answer(event, out)
    if agent == "cursor":
        return cursor_answer(event, out, data)
    return gemini_answer(event, out, data) if agent == "gemini" else out


# --- install / uninstall into Codex's hooks.json ---

def codex_home():
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def _ours(handler):
    return isinstance(handler, dict) and "--agent codex" in str(handler.get("command", "")) \
        and " hook " in str(handler.get("command", ""))


def codex_command(program, event):
    """The hook command. A missing or broken program makes the shell exit non-zero: the fallback then exits 2 with a
    reason on stderr, which Codex treats as a block for tool calls (a plain error would let the call through).
    Prompts and session starts never block on that (never lock you out): Codex shows the error and goes on."""
    cmd = f"{shlex.quote(program)} hook {event} --agent codex"
    if event in ("pre", "post"):
        cmd += f" || /bin/sh -c 'echo \"{FAILED}\" >&2; exit 2'"
    return cmd


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError(f"{path} is not a hooks file this version understands; left as it is")
    return data


def _strip(data):
    """Our handlers taken out; groups and events left empty by that go too. Other hooks stay as they are."""
    hooks = data.get("hooks", {})
    for name, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        kept = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list) and any(_ours(h) for h in g["hooks"]):
                g = {**g, "hooks": [h for h in g["hooks"] if not _ours(h)]}
                if not g["hooks"]:
                    continue
            kept.append(g)
        if kept:
            hooks[name] = kept
        else:
            del hooks[name]
    return data


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".cloakroom-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def install_codex(program):
    path = os.path.join(codex_home(), "hooks.json")
    data = _strip(_load(path))
    hooks = data.setdefault("hooks", {})
    for event, name in HOOK_EVENTS.items():
        hooks.setdefault(name, []).append({"hooks": [{
            "type": "command", "command": codex_command(program, event), "timeout": TIMEOUTS[event],
            "statusMessage": "Cloakroom: checking"}]})
    _write(path, data)
    return path


def uninstall_codex():
    path = os.path.join(codex_home(), "hooks.json")
    if not os.path.exists(path):
        return path
    data = _strip(_load(path))
    if data.get("hooks") or set(data) - {"hooks"}:
        _write(path, data)
    else:
        os.remove(path)  # the file held only our hooks
    return path


def codex_installed():
    """True when Codex's hooks.json holds Cloakroom's hooks for all four events."""
    try:
        hooks = _load(os.path.join(codex_home(), "hooks.json")).get("hooks", {})
    except (ValueError, OSError):
        return False
    return all(any(_ours(h) for g in hooks.get(n, []) if isinstance(g, dict) for h in g.get("hooks", []))
               for n in HOOK_EVENTS.values())


# --- install / uninstall into Cursor's hooks.json (flat lists of entries, not Codex's groups) ---

def cursor_home():
    return os.path.join(os.path.expanduser("~"), ".cursor")


def cursor_command(program, event):
    """No `|| exit 2` fallback as for Codex: failClosed turns any failure of a tool hook into a deny."""
    return f"{shlex.quote(program)} hook {event} --agent cursor"


def _cursor_ours(entry):
    return isinstance(entry, dict) and " hook " in str(entry.get("command", "")) \
        and "--agent cursor" in str(entry.get("command", ""))


def _cursor_load(path):
    data = _load(path)
    if not all(isinstance(v, list) for v in data.get("hooks", {}).values()):
        raise ValueError(f"{path} is not a hooks file this version understands; left as it is")
    return data


def _cursor_strip(data):
    """Our entries taken out; events left empty by that go too. Other hooks stay as they are."""
    hooks = data.get("hooks", {})
    for name, entries in list(hooks.items()):
        kept = [e for e in entries if not _cursor_ours(e)]
        if kept:
            hooks[name] = kept
        else:
            del hooks[name]
    return data


def install_cursor(program):
    path = os.path.join(cursor_home(), "hooks.json")
    data = _cursor_strip(_cursor_load(path))
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    for name, (event, matcher, fail_closed) in CURSOR_HOOKS.items():
        entry = {"command": cursor_command(program, event)}
        if matcher:
            entry["matcher"] = matcher
        if fail_closed:
            entry["failClosed"] = True
        hooks.setdefault(name, []).append(entry)
    _write(path, data)
    return path


def uninstall_cursor():
    path = os.path.join(cursor_home(), "hooks.json")
    if not os.path.exists(path):
        return path
    data = _cursor_strip(_cursor_load(path))
    if data.get("hooks") or set(data) - {"version", "hooks"}:
        _write(path, data)
    else:
        os.remove(path)  # the file held only our hooks
    return path


def cursor_installed():
    """True when Cursor's hooks.json holds Cloakroom's hooks for all nine events."""
    try:
        hooks = _cursor_load(os.path.join(cursor_home(), "hooks.json")).get("hooks", {})
    except (ValueError, OSError):
        return False
    return all(any(_cursor_ours(e) for e in hooks.get(n, [])) for n in CURSOR_HOOKS)


def cursor_other_hooks(cwd):
    """Files other than the user file that hold a hook on Cursor's tool calls that isn't ours (the project file and
    the enterprise file): one of them may change a command or a read too, and which change wins is unknown."""
    enterprise = ("/Library/Application Support/Cursor/hooks.json" if sys.platform == "darwin" else
                  "C:\\ProgramData\\Cursor\\hooks.json" if os.name == "nt" else "/etc/cursor/hooks.json")
    found = []
    for path in (os.path.join(cwd, ".cursor", "hooks.json"), enterprise):
        try:
            hooks = _cursor_load(path).get("hooks", {})
        except (ValueError, OSError):
            continue
        if any(not _cursor_ours(e) for n in ("preToolUse", "beforeShellExecution") for e in hooks.get(n, [])):
            found.append(path)
    return found


def _program():
    """The program running this command (the one that knows --agent codex), else where the plugin would find it."""
    me = os.path.abspath(sys.argv[0])  # not realpath: a uv or pipx link keeps working across upgrades
    if os.path.basename(me).startswith("cloakroom") and os.path.isfile(me) and os.access(me, os.X_OK):
        return me
    from .cli import find_program
    return find_program()


# --- install / uninstall Gemini CLI's extension ---

def gemini_home():
    return os.path.join(os.environ.get("GEMINI_CLI_HOME") or os.path.expanduser("~"), ".gemini")


def gemini_dir():
    """An extension folder of our own, not ~/.gemini/settings.json: Gemini skips settings.json hooks (yours too) in a
    folder you haven't trusted, but runs an extension's hooks everywhere."""
    return os.path.join(gemini_home(), "extensions", "cloakroom")


def gemini_command(program, event):
    """Tool calls: a missing or broken program exits 2 with a reason, which Gemini treats as a block (any other
    failure is a warning and the call runs). Prompts and session starts never block on that (never lock you out):
    the fallback prints a warning as JSON, since Gemini also blocks on a plain-text answer with another exit code."""
    cmd = f"{shlex.quote(program)} hook {event} --agent gemini"
    if event in ("pre", "post"):
        return cmd + f" || /bin/sh -c 'echo \"{FAILED}\" >&2; exit 2'"
    return cmd + " || echo '" + json.dumps({"systemMessage": NOT_CHECKED}) + "'"


def _gemini_ours():
    """True when the folder holds Cloakroom's extension (and not someone else's "cloakroom")."""
    try:
        with open(os.path.join(gemini_dir(), "hooks", "hooks.json"), encoding="utf-8") as f:
            hooks = json.load(f).get("hooks", {})
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(hooks, dict) and all(
        any(isinstance(h, dict) and "--agent gemini" in str(h.get("command", "")) for g in hooks.get(n) or []
            if isinstance(g, dict) for h in g.get("hooks") or []) for n in GEMINI_EVENTS.values())


def install_gemini(program):
    from . import __version__
    path = gemini_dir()
    if os.path.lexists(path) and not _gemini_ours():
        raise ValueError(f"{path} exists and isn't Cloakroom's extension; left as it is")
    _write(os.path.join(path, "gemini-extension.json"), {
        "name": "cloakroom", "version": __version__,
        "description": "Cloakroom: masks secrets and personal data in tool output; written by cloakroom install gemini"})
    _write(os.path.join(path, "hooks", "hooks.json"), {"hooks": {name: [{"hooks": [{
        "type": "command", "name": f"cloakroom-{event}", "command": gemini_command(program, event),
        "timeout": TIMEOUTS[event] * 1000}]}] for event, name in GEMINI_EVENTS.items()}})
    return path


def uninstall_gemini():
    path = gemini_dir()
    if not os.path.lexists(path):
        return path
    if not _gemini_ours():
        raise ValueError(f"{path} isn't Cloakroom's extension; left as it is")
    import shutil
    shutil.rmtree(path)
    return path


def gemini_installed():
    return _gemini_ours()


_JSONC = re.compile(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', re.DOTALL)  # Gemini's settings may hold comments


def _jsonc(path):
    """A Gemini JSON file, comments allowed: None when it's absent, ValueError when it can't be read."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ValueError(str(e)) from e
    data = json.loads(_JSONC.sub(lambda m: m.group(1) or "", text))
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _gemini_enabled_here(rules, cwd):
    """Gemini's extension enablement rules (extensionEnablement.ts): "!" disables, a trailing * covers subfolders,
    the last rule that matches the folder wins, enabled when none does."""
    def folder(p):  # with leading and trailing slashes, as Gemini compares them
        p = p.replace("\\", "/").strip("/")
        return f"/{p}/" if p else "/"
    here = folder(os.path.abspath(cwd))
    on = True
    for rule in rules:
        off = rule.startswith("!")
        base = rule[1:] if off else rule
        sub = base.endswith("*")
        base = base[:-1] if sub else base
        base = folder(base)
        if here == base or (sub and here.startswith(base)):
            on = not off
    return on


def gemini_problems(cwd):
    """Why Gemini CLI may not run Cloakroom's hooks: the extension disabled (gemini extensions disable), hooks off
    (hooksConfig.enabled false) or ours in hooksConfig.disabled. One plain sentence each; "can't tell" when a file
    can't be read. [] when none applies (a -e flag on Gemini's command line can't be seen from here)."""
    out = []
    path = os.path.join(gemini_home(), "extensions", "extension-enablement.json")
    try:
        rules = ((_jsonc(path) or {}).get("cloakroom") or {}).get("overrides") or []
        if not _gemini_enabled_here([str(r) for r in rules], cwd):
            out.append("Cloakroom's extension is disabled in this folder, so Gemini doesn't run its hooks here. "
                       "To turn it on: gemini extensions enable cloakroom")
        elif any(str(r).startswith("!") for r in rules):
            out.append("Cloakroom's extension is disabled in some folders, where Gemini doesn't run its hooks "
                       "(gemini extensions list shows which)")
    except (ValueError, AttributeError):
        out.append(f"can't tell whether Cloakroom's extension is enabled: {path} can't be read")
    system = os.environ.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH") or (
        "/Library/Application Support/GeminiCli/settings.json" if sys.platform == "darwin" else
        "C:\\ProgramData\\gemini-cli\\settings.json" if os.name == "nt" else "/etc/gemini-cli/settings.json")
    enabled, where = None, ""
    for path in (os.path.join(gemini_home(), "settings.json"), os.path.join(cwd, ".gemini", "settings.json"), system):
        try:
            hc = (_jsonc(path) or {}).get("hooksConfig") or {}
            if not isinstance(hc, dict):
                raise ValueError("hooksConfig is not an object")
        except ValueError:
            out.append(f"can't tell whether Gemini's hooks are on: {path} can't be read")
            continue
        if isinstance(hc.get("enabled"), bool):  # user, then workspace, then system: the last one set wins
            enabled, where = hc["enabled"], path
        if any("cloakroom" in str(h) for h in hc.get("disabled") or []):
            out.append(f"Cloakroom's hooks are listed in hooksConfig.disabled in {path}, so Gemini skips them")
    if enabled is False:
        out.append(f"Gemini's hooks are switched off (hooksConfig.enabled is false in {where}), so it runs none of "
                   "Cloakroom's hooks")
    return out


def install(agent, remove=False):
    """cloakroom install|uninstall <agent>."""
    if agent not in NAMES:
        print(f"cloakroom: {agent} isn't supported yet (Claude Code installs as a plugin)", file=sys.stderr)
        return 2
    if os.name == "nt":
        print(f"cloakroom: {NAMES[agent]} on Windows isn't supported yet", file=sys.stderr)
        return 1
    try:
        if remove:
            if agent == "gemini":
                path = uninstall_gemini()
                print(f"cloakroom: Gemini CLI extension removed ({path})")
                return 0
            if agent == "cursor":
                print(f"cloakroom: Cursor hooks removed from {uninstall_cursor()}")
                return 0
            print(f"cloakroom: Codex hooks removed from {uninstall_codex()}")
            return 0
        program = _program()
        if not program:
            print(f"cloakroom: can't find the cloakroom program to point {NAMES[agent]} at; install it first "
                  "(uv tool install 'cloakroom[model,torch]')", file=sys.stderr)
            return 1
        path = {"gemini": install_gemini, "cursor": install_cursor}.get(agent, install_codex)(program)
    except (ValueError, OSError) as e:
        print(f"cloakroom: {e}", file=sys.stderr)
        return 1
    if agent == "gemini":
        print(f"cloakroom: wrote a Gemini CLI extension named cloakroom to {path}\n"
              f"(gemini-extension.json and hooks/hooks.json, four hooks running {program}).\n"
              "No other Gemini file was changed. Gemini runs the hooks in every folder from its next start on;\n"
              "gemini extensions list shows it. To remove it: cloakroom uninstall gemini")
        return 0
    if agent == "cursor":
        print(f"cloakroom: Cursor hooks written to {path} (program: {program}).\n"
              "Cursor's command-line agent (agent) runs them from its next start. The editor reads the same file but "
              "hasn't been checked with Cloakroom yet.\n"
              "To remove them: cloakroom uninstall cursor")
        return 0
    print(f"cloakroom: Codex hooks written to {path} (program: {program}).\n"
          "Next, once: start Codex (codex), type /hooks, open each of the four Cloakroom hooks and trust it.\n"
          "Until you do, Codex skips them, and codex exec does so without a warning: nothing is masked.\n"
          "To remove them: cloakroom uninstall codex")
    return 0
