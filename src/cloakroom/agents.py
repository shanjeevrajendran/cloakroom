"""Coding agents other than Claude Code. hook.py speaks Claude Code's hook JSON; this module turns its answers into
another agent's, and installs the hooks into that agent's config.

Codex (https://developers.openai.com/codex/hooks) sends the same input field names as Claude Code (session_id, cwd,
prompt, tool_name, tool_input, tool_response, tool_use_id), and tool names Bash, apply_patch and mcp__server__tool,
so only the answer needs translating:
  - a tool result can't be replaced field by field, but a PostToolUse "block" replaces the result the model sees with
    its reason: the masked output goes there;
  - a rewritten tool input needs permissionDecision "allow"; "ask" isn't supported, so an ask becomes a deny.
Standard library only: hookentry imports this before anything else can fail.
"""
import json
import os
import re
import shlex
import sys

AGENTS = ("claude", "codex")
HOOK_EVENTS = {"start": "SessionStart", "prompt": "UserPromptSubmit", "pre": "PreToolUse", "post": "PostToolUse"}
TIMEOUTS = {"start": 30, "prompt": 60, "pre": 60, "post": 120}  # seconds, as for Claude Code
FAILED = ("Cloakroom is not installed or not working, so this was stopped to keep data private. Run: cloakroom doctor")
CODEX_GUIDE = ("Cloakroom note: values like PII_EMAIL_1_k7q2ab are placeholders for real data kept on this machine. Use "
               "them verbatim in apply_patch and in local shell commands (cat, grep, sed, python...): Cloakroom puts "
               "the real values back before the command runs and masks them again in the output. Copy placeholders "
               "exactly, tag included: Cloakroom restores only the ones it issued in this session. Don't try to "
               "recover the real values.")
ASK_NOTE = " Codex can't ask you from a hook, so the call was stopped; run it yourself if it's what you want."
_PROMPT = re.compile(r"^Cloakroom blocked this prompt: it contains (.+?)\. ")


def _codex_words(text):
    """Cloakroom's messages name Claude; on Codex they name Codex."""
    if not isinstance(text, str):
        return text
    return (text.replace("restart Claude Code (/exit, then claude)", "restart Codex")
            .replace("Claude Code", "Codex").replace("Claude", "Codex"))


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
        m = _PROMPT.match(out.get("reason", ""))
        out["reason"] = (f"Cloakroom stopped this message: it holds {m.group(1)}. To go on, put that detail in a file "
                         "and ask Codex to read it (Cloakroom masks it on the way in), or take it out and send again. "
                         "To send it as it is, start the message with cr!" if m else _codex_words(out["reason"]))
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


def answer(agent, event, out):
    return codex_answer(event, out) if agent == "codex" else out


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


def install(agent, remove=False):
    """cloakroom install|uninstall <agent>."""
    if agent != "codex":
        print(f"cloakroom: {agent} isn't supported yet (Claude Code installs as a plugin)", file=sys.stderr)
        return 2
    if os.name == "nt":
        print("cloakroom: Codex on Windows isn't supported yet", file=sys.stderr)
        return 1
    try:
        if remove:
            print(f"cloakroom: Codex hooks removed from {uninstall_codex()}")
            return 0
        program = _program()
        if not program:
            print("cloakroom: can't find the cloakroom program to point Codex at; install it first "
                  "(uv tool install 'cloakroom[model,torch]')", file=sys.stderr)
            return 1
        path = install_codex(program)
    except (ValueError, OSError) as e:
        print(f"cloakroom: {e}", file=sys.stderr)
        return 1
    print(f"cloakroom: Codex hooks written to {path} (program: {program}).\n"
          "Next, once: start Codex (codex), type /hooks, open each of the four Cloakroom hooks and trust it.\n"
          "Until you do, Codex skips them, and codex exec does so without a warning: nothing is masked.\n"
          "To remove them: cloakroom uninstall codex")
    return 0
