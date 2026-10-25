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
Standard library only: hookentry imports this before anything else can fail.
"""
import json
import os
import re
import shlex
import sys

AGENTS = ("claude", "codex", "gemini")
NAMES = {"codex": "Codex", "gemini": "Gemini CLI"}
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
HELD, NOTE = "_cloakroom_held", "_cloakroom_note"  # set by gemini_request for gemini_answer
NOT_CHECKED = ("cloakroom: not working, so this message was not checked. Tool calls stay blocked until it works "
               "again. Run: cloakroom doctor")
ASK_NOTE = " Codex can't ask you from a hook, so the call was stopped; run it yourself if it's what you want."
_PROMPT = re.compile(r"^Cloakroom blocked this prompt: it contains (.+?)\. ")


def _words(text, name):
    """Cloakroom's messages name Claude; on another agent they name that agent."""
    if not isinstance(text, str):
        return text
    return (text.replace("restart Claude Code (/exit, then claude)", f"restart {name}")
            .replace("Claude Code", name).replace("Claude", name))


def _codex_words(text):
    return _words(text, "Codex")


def _prompt_reason(reason, name):
    m = _PROMPT.match(reason or "")
    return (f"Cloakroom stopped this message: it holds {m.group(1)}. To go on, put that detail in a file and ask {name} "
            "to read it (Cloakroom masks it on the way in), or take it out and send again. To send it as it is, "
            "start the message with cr!" if m else _words(reason, name))


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
    note = _rules_until_warm()
    if note:
        data[NOTE] = note
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
    the service answers, this call runs on the rules in-process, and the service is started for the next one."""
    from . import client, config
    cfg = config.load()
    if cfg["mode"] != "full" or client.health(cfg["port"]) is not None:
        return ""
    import socket
    try:  # something listening but not answering yet: the service is loading the model
        socket.create_connection(("127.0.0.1", cfg["port"]), 0.5).close()
    except OSError:
        client.start_background()
    os.environ["CLOAKROOM_MODE"] = "rules"  # this process only
    return ("cloakroom: the local model is still starting, so this was checked with the rules only (keys, cards, "
            "IDs; names and addresses may be missed)")


def gemini_answer(event, out, data=None):
    """hook.py's Claude Code answer as Gemini's. None or {} stays as it is: a clean pass."""
    data = data if isinstance(data, dict) else {}
    held = event == "post" and data.get(HELD)
    if not out and not held and not data.get(NOTE):
        return out
    out = dict(out or {})
    name = NAMES["gemini"]
    if data.get(NOTE):
        out["systemMessage"] = data[NOTE] + (" · " + out["systemMessage"] if out.get("systemMessage") else "")
    for k in ("systemMessage", "stopReason"):
        if k in out:
            out[k] = _words(out[k], name)
    if out.get("decision") == "block":  # a prompt
        out.update(decision="deny", reason=_prompt_reason(out.get("reason", ""), name))
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


def request(agent, event, data):
    return gemini_request(event, data) if agent == "gemini" else data


def answer(agent, event, out, data=None):
    if agent == "codex":
        return codex_answer(event, out)
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
            print(f"cloakroom: Codex hooks removed from {uninstall_codex()}")
            return 0
        program = _program()
        if not program:
            print(f"cloakroom: can't find the cloakroom program to point {NAMES[agent]} at; install it first "
                  "(uv tool install 'cloakroom[model,torch]')", file=sys.stderr)
            return 1
        path = install_gemini(program) if agent == "gemini" else install_codex(program)
    except (ValueError, OSError) as e:
        print(f"cloakroom: {e}", file=sys.stderr)
        return 1
    if agent == "gemini":
        print(f"cloakroom: wrote a Gemini CLI extension named cloakroom to {path}\n"
              f"(gemini-extension.json and hooks/hooks.json, four hooks running {program}).\n"
              "No other Gemini file was changed. Gemini runs the hooks in every folder from its next start on;\n"
              "gemini extensions list shows it. To remove it: cloakroom uninstall gemini")
        return 0
    print(f"cloakroom: Codex hooks written to {path} (program: {program}).\n"
          "Next, once: start Codex (codex), type /hooks, open each of the four Cloakroom hooks and trust it.\n"
          "Until you do, Codex skips them, and codex exec does so without a warning: nothing is masked.\n"
          "To remove them: cloakroom uninstall codex")
    return 0
