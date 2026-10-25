"""Claude Code hooks. Reads the hook JSON on stdin, prints the hook's JSON answer.

  prompt  (UserPromptSubmit): block prompts holding secrets/PII - a prompt can't be rewritten, only blocked.
  post    (PostToolUse): mask every string in the tool result before Claude sees it.
  pre     (PreToolUse): local tools get real values back; Bash only when safe; outbound tools keep placeholders
                        and are denied if they carry a real value.

Fail-closed: any error blocks the prompt, withholds the output, or denies the call.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

from . import client, config, crypto, decode, engine, events, filelog, media, money, shell
from .hookentry import STRUCTURAL, _withhold, closed
from .vault import ANY_PLACEHOLDER, OLD_FORMAT, PLACEHOLDER, Vault

SHELL_TOOLS = {"Bash", "PowerShell"}
LOCAL_FILE_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "NotebookRead", "Glob", "Grep", "LS",
                    "apply_patch"}  # apply_patch: Codex's file edits (agents.py)
GUIDE = ("Cloakroom note: values like PII_EMAIL_1_k7q2ab are placeholders for real data kept on this machine. Use them "
         "verbatim in Write, Grep and local Bash commands, and in Edit's new_string: Cloakroom restores the real "
         "values before the tool runs and masks them again in the output. Edit's old_string must not contain a "
         "placeholder (the file holds the real value, so it won't match): anchor old_string on nearby text without "
         "placeholders, or rewrite the file with Write. Copy placeholders exactly, tag included: Cloakroom restores "
         "only the ones it issued in this session. Don't try to recover the real values.")
PRIME_MAX_BYTES = 512 * 1024
_ACTION = [""]  # set when a prompt passes by your own choice (sent anyway, paused), for the event log
_NOTE = [""]  # extra detail for the hook.log line (OCR pages and time), never values
_FOUND: list[tuple[str, str]] = []  # (placeholder, kind) found during the current hook call, for the event log
_EXTRA: dict = {}  # extra event-log fields for this hook call (session start: version, mode, hooks)
_SKIPS: list[tuple] = []  # checks this hook call let through without running in full: (reason, check, what, say, extra)
TIP = ("Cloakroom can't rewrite a prompt, only block it. Put the detail in a file and ask Claude to read it: "
       "it will be masked on the way in and restored for local edits. Send it anyway: add cr! at the start. "
       "Pause prompt checks: type cr: pause 10m (tool output stays masked). Resume: cr: resume.")
_ALLOW_CMD = re.compile(r"(?i)^\s*cr:\s*allow(?:\s+once)?\s*$")
HOLD_MINUTES = 10  # how long a held command can be allowed with cr: allow
_PAUSE_CMD = re.compile(r"(?i)^\s*cr:\s*(pause(?:\s+(\d{1,3})\s*m(?:in(?:utes?)?)?)?|resume)\s*$")


def _strings(value, fn):
    """Apply fn to every string leaf; returns (new value, changed)."""
    if isinstance(value, str):
        new = fn(value)
        return new, new != value
    if isinstance(value, dict):
        out, changed = {}, False
        for k, v in value.items():
            if k in STRUCTURAL:  # never touch fields Claude Code needs to accept the result
                out[k] = v
                continue
            out[k], c = _strings(v, fn)
            changed |= c
        return out, changed
    if isinstance(value, list):
        out, changed = [], False
        for v in value:
            nv, c = _strings(v, fn)
            out.append(nv)
            changed |= c
        return out, changed
    return value, False


def _skip(reason: str, check: str, what: str = "", say: bool = True, ms: float = 0.0, **numbers):
    """A check that let this call through without running in full (fail-open): logged as a "skipped" event (no
    values) and, when say, told to you once per session for each reason and check. what: the check in the message."""
    _SKIPS.append((reason, check, what or f"this {check}", say, {"ms": round(ms, 1), **numbers}))


def _skip_message(session: str) -> str:
    """The first-in-this-session skip notes, e.g. "Cloakroom didn't check this git push: it was larger than 4 MB"."""
    out = []
    for reason, check, what, say, numbers in _SKIPS:
        if not say:
            continue
        safe = "".join(c for c in session if c.isalnum() or c in "-_")[:80] or "default"
        flag = config.private_dir("skip-noted") / f"{safe}.{reason}.{''.join(c for c in check if c.isalnum())}"
        if flag.exists():
            continue
        flag.touch(mode=0o600)
        msg = f"Cloakroom didn't check {what}: {events.skip_why({'reason': reason, **numbers})}"
        if msg not in out:
            out.append(msg)
    return " · ".join(out)


def _summary(kinds: list[str]) -> str:
    counts = {}
    for k in kinds:
        counts[k] = counts.get(k, 0) + 1
    return ", ".join(f"{k} x{n}" for k, n in sorted(counts.items()))


def _dry(event: str, note: str) -> bool:
    if not os.environ.get("CLOAKROOM_DRY_RUN"):
        return False
    _ACTION[0] = "dry-run"
    with open(config.private_dir() / "dry-run.log", "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {event} {note}\n")
    return True


PROMPT_OK = {"PERSON"}  # names you type yourself go through; Cloakroom can only block a prompt, not mask it


def _pause_file():
    return config.private_dir() / "pause.json"


def paused_minutes() -> int:
    """Minutes left on a prompt-check pause you started with `cr: pause`, 0 when not paused."""
    try:
        left = json.loads(_pause_file().read_text())["until"] - time.time()
    except (OSError, ValueError, KeyError):
        return 0
    return max(0, -(-int(left) // 60)) if left > 0 else 0


def _control(prompt: str) -> dict | None:
    """Your own controls, typed as a prompt. They are answered here and never reach Claude (a block is the only way
    a prompt hook can stop a prompt), so only you can pause: Claude never sees or sends these."""
    if _ALLOW_CMD.match(prompt):
        _ACTION[0] = "allow armed"
        return {"decision": "block", "reason": _arm_held()}
    m = _PAUSE_CMD.match(prompt)
    if not m:
        return None
    if m.group(1).lower() == "resume":
        _ACTION[0] = "pause off"
        _pause_file().unlink(missing_ok=True)
        return {"decision": "block", "reason": "Cloakroom: prompt checks are back on."}
    minutes = min(int(m.group(2) or 10), 240)
    _ACTION[0] = "pause on"
    fd = os.open(_pause_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"until": time.time() + 60 * minutes}, f)
    return {"decision": "block", "reason": f"Cloakroom: prompt checks paused for {minutes} min. Tool output is still "
                                           "masked. Turn checks back on early: cr: resume."}


def _held_file():
    return config.private_dir() / "held.json"


def _command_mac(command: str) -> str:
    """A keyed hash of a held command, so the file never holds the command (or the secret in it)."""
    import hashlib
    import hmac
    return hmac.new(config.token().encode(), command.encode(), hashlib.sha256).hexdigest()


def _write_held(state: dict):
    fd = os.open(_held_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f)


def _read_held() -> dict | None:
    try:
        state = json.loads(_held_file().read_text())
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and state.get("until", 0) > time.time() else None


def _arm_held() -> str:
    """cr: allow - let the last held command run once (Claude can't type this: it is your prompt)."""
    state = _read_held()
    if not state:
        return "Cloakroom: no command is being held right now, so there is nothing to allow."
    state["armed"] = True
    _write_held(state)
    return ("Cloakroom: the held command may run once. Ask Claude to run it again, unchanged; a changed command is "
            "held again.")


def _allowed_once(command: str) -> bool:
    """The command you allowed with cr: allow, run unchanged: it passes this one time."""
    state = _read_held()
    if not (state and state.get("armed") and state.get("mac") == _command_mac(command)):
        return False
    _held_file().unlink(missing_ok=True)
    return True


def _egress_hold(command: str, session: str, vault: Vault, powershell: bool, money_why: str = "") -> dict | None:
    """Hold a command that would send a known secret off this machine: a value already in the session vault (plain
    or encoded), a key the rules recognise, or a literal token in a credential header. Labels only in the reason.
    money_why: the command would also move money (money.reason): one hold names both, one cr: allow covers it."""
    via = shell.egress(command, powershell)
    if not via:
        return _money_hold(command, money_why) if money_why else None
    cfg = config.load()
    found, spans = [], []
    by_value = vault.by_value
    known = {v: k for v, k in engine.known_values(vault, cfg["always"]).items() if k in engine.KNOWN_KINDS}
    for a, b, value, how in decode.find_known(command, known):
        ph = by_value.get(value)
        label = ph or "one of your always-mask values"
        found.append(label if how == "plain" else f"{label} ({how})")
        spans.append((a, b))
        _FOUND.append((ph or "", ph.split("_")[1] if ph else "SECRET"))
    literal = [] if found else shell.literal_tokens(command)  # a literal token the rules may not know
    for where in literal:
        found.append(f"a literal token in an {where}" if where[0].lower() in "aeioux" else
                     f"a literal token in a {where}")
        _FOUND.append(("", "SECRET"))
    keys, _ = engine.detect(command, "rules", cfg["allow"], cfg["always"])
    for f in keys:
        if f.kind not in ("SECRET", "PASSWORD") or any(a < f.end and f.start < b for a, b in spans):
            continue
        if literal and f.source == "keyed-secret":  # the same header value, already named above
            continue
        found.append(f"a {f.kind} ({f.source})")
        _FOUND.append(("", f.kind))
    if not found:
        return _money_hold(command, money_why) if money_why else None
    if money_why:
        _NOTE[0], _EXTRA["money"] = f"money: {money_why}", money_why
    if _allowed_once(command):
        _ACTION[0] = "allowed once"
        return None
    what = ", ".join(dict.fromkeys(found))
    if _dry("pre", f"egress via {via}: would hold"):
        return None
    _write_held({"mac": _command_mac(command), "until": time.time() + 60 * HOLD_MINUTES, "armed": False})
    _ACTION[0] = "held"
    also = f" and it would move money ({money_why})" if money_why else ""
    return _deny(f"Cloakroom held this command: it would send {what} off this machine (via {via}){also}. A secret that "
                 f"has left can't be called back. To send it anyway, type cr: allow, then ask Claude to run the same "
                 f"command again: it passes once, within {HOLD_MINUTES} min. Claude: don't change or re-encode the "
                 f"value to get past this; use an environment variable the user sets, or ask the user.")


def _money_hold(key: str, why: str) -> dict | None:
    """Hold a call that would move money (money.py) until you type cr: allow; key is the command, or the tool name and
    its input, so only the same call passes once."""
    _NOTE[0], _EXTRA["money"] = f"money: {why}", why
    if _allowed_once(key):
        _ACTION[0] = "allowed once"
        return None
    if _dry("pre", f"money ({why}): would hold"):
        return None
    _write_held({"mac": _command_mac(key), "until": time.time() + 60 * HOLD_MINUTES, "armed": False})
    _ACTION[0] = "held"
    return _deny(f"Cloakroom held this call: it would move money ({why}). To run it, type cr: allow, then ask Claude "
                 f"to run the same call again: it passes once, within {HOLD_MINUTES} min. Claude: don't change the "
                 f"call to get past this; ask the user.")


def _long_hold(command: str) -> dict | None:
    """Codex, with codex_hold_long_running on: hold a command that obviously keeps running (shell.long_running).
    Codex stops waiting on it after a while and runs no PostToolUse hook on what it prints after that, so that
    output would reach the model unmasked. Held events carry hold=long-running, so false holds can be counted."""
    what = shell.long_running(command)
    if not what:
        return None
    _NOTE[0], _EXTRA["hold"] = f"long-running: {what}", "long-running"
    if _allowed_once(command):
        _ACTION[0] = "allowed once"
        return None
    if _dry("pre", f"long-running ({what}): would hold"):
        return None
    _write_held({"mac": _command_mac(command), "until": time.time() + 60 * HOLD_MINUTES, "armed": False})
    _ACTION[0] = "held"
    return _deny(f"Cloakroom held this command: {what} keeps running, and what it prints after Codex stops waiting "
                 f"won't be masked. To run it anyway, type cr: allow, then ask Codex to run the same command again: it "
                 f"passes once, within {HOLD_MINUTES} min. Codex: don't change the command to get past this; ask the "
                 "user.")


def on_prompt(data: dict) -> dict | None:
    out = _on_prompt(data)
    note = _install_note_once(data.get("session_id", "default")) or crypto.denial_notice()
    if note:
        out = dict(out or {})
        out["systemMessage"] = note + (" · " + out["systemMessage"] if out.get("systemMessage") else "")
    return out


def _on_prompt(data: dict) -> dict | None:
    prompt = data.get("prompt", "")
    control = _control(prompt)
    if control:
        return control
    if prompt.lstrip().lower().startswith("cr!"):  # you chose to send this one prompt as it is
        _ACTION[0] = "sent anyway"
        return None
    left = paused_minutes()
    if left:
        _ACTION[0] = "paused"
        return {"systemMessage": f"cloakroom: prompt checks paused, {left} min left (cr: resume to turn them back on)"}
    r = client.mask(prompt, data.get("session_id", "default"))
    # A prompt holding only names passes; with an email, phone, card, key... beside them it is blocked as a whole
    # (that is what a pasted customer list looks like), names included.
    if not r["masked"] or all(k in PROMPT_OK for _, k, _ in r["masked"]):
        return None
    _FOUND.extend((p, k) for p, k, _ in r["masked"])
    kinds = _summary([k for _, k, _ in r["masked"]])
    if _dry("prompt", f"would block: {kinds}"):
        return None
    return {"decision": "block", "reason": f"Cloakroom blocked this prompt: it contains {kinds}. {TIP}"}


def on_post(data: dict) -> dict | None:
    session, kinds = data.get("session_id", "default"), []
    if data.get("tool_use_id"):  # a restoring write ran: log where its real values landed (filelog.py)
        filelog.complete(str(data["tool_use_id"]), Vault(config.vault_path(session)))
    roots = [data.get("cwd") or os.getcwd()]
    read = str((data.get("tool_input") or {}).get("file_path") or "") if data.get("tool_name") == "Read" else ""

    def fn(s):
        if len(s) < 4:
            return s
        r = client.mask(s, session, ocr=converted == "ocr", roots=roots, read=read)
        kinds.extend(k for _, k, _ in r["masked"])
        _FOUND.extend((p, k) for p, k, _ in r["masked"])
        return r["text"]
    raw, converted = data.get("tool_response"), ""
    if data.get("tool_name") == "Read":  # PDFs become their text layer, scans and pictures OCR text (media.py)
        cfg = config.load()
        ocr = media.Ocr.from_config(cfg)
        raw, converted = media.convert(raw, (data.get("tool_input") or {}).get("file_path", ""),
                                       allow_images=bool(cfg.get("allow_images")), ocr=ocr)
        if ocr.pages or ocr.error:
            _NOTE[0] = (f"ocr pages={ocr.pages} ms={ocr.ms:.0f}" if not ocr.error else
                        f"ocr failed ({ocr.error})")
        if converted == "image-allowed":
            _ACTION[0] = "image passed"
            return {"systemMessage": "cloakroom: an image went to Claude unchecked (allow_images is on)"}
        if converted == "held":
            _ACTION[0] = "withheld"
            if _dry("post", "Read: would hold back a non-text result"):
                return None
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": raw},
                    "systemMessage": "cloakroom: held back a Read result it can't check (image or PDF without text)"}
    img = ""  # what happened to MCP images, for every message below
    if str(data.get("tool_name", "")).startswith("mcp__"):  # screenshots and pictures: OCR text, never pixels
        cfg = config.load()
        ocr = media.Ocr.from_config(cfg)
        raw, n = media.convert_mcp(raw, allow_images=bool(cfg.get("allow_images")), ocr=ocr)
        if ocr.pages or ocr.error:
            _NOTE[0] = f"ocr pages={ocr.pages} ms={ocr.ms:.0f}" + (f" ocr failed ({ocr.error})" if ocr.error else "")
        img = "; ".join(x for x in (
            f"{n['ocr']} image(s) read by local OCR, the picture not sent" if n["ocr"] else "",
            f"{n['held']} image(s) held back (no OCR text)" if n["held"] else "",
            f"{n['allowed']} image(s) sent unchecked (allow_images is on)" if n["allowed"] else "") if x)
        if n["ocr"]:
            converted = "ocr"  # the OCR text gets OCR masking (digit runs), like a Read picture
        elif n["held"]:
            converted = "held"
        if n["allowed"] and not (n["ocr"] or n["held"]):
            _ACTION[0] = "image passed"
    new, changed = _strings(raw, fn)
    # Encoded output (od -c, xxd, base64, rev...) can carry values the detectors can't read: decode and re-check
    # each text field, and replace only the fields that hit (structural fields must stay as they are).
    # dates are left out: a fragment like "Oct 11 12:16" repeats harmlessly across a file listing or a log
    known = [v for ph, v in Vault(config.vault_path(session)).by_placeholder.items() if "_DATE_" not in ph]
    cmd = (data.get("tool_input") or {}).get("command", "")
    reverse = bool(re.search(r"\brev\b|\[array\]::Reverse|-join\s*\(.*\[-1", cmd))
    msg = ("[Cloakroom withheld this output: it holds secrets or personal data in an encoded or spaced-out "
           "form it can't mask safely. Read the file directly instead; Cloakroom masks it on the way in.]")
    hits = []

    def check(t):
        if len(t) < 6:
            return t
        hit = decode.known_value_hit(t, known)
        for view in ([] if hit else decode.views(t, reverse=reverse)):
            if decode.known_value_hit(view, known) or client.mask(view, session, roots=roots)["masked"]:
                hit = True
                break
        if hit:
            hits.append(1)
            return msg
        return t
    new, _ = _strings(new, check)
    tail = f" (MCP: {img})" if img else ""
    mcp = str(data.get("tool_name", "")).startswith("mcp__")

    def output(value):  # MCP output: also the older field, for Claude Code versions before updatedToolOutput
        o = {"hookEventName": "PostToolUse", "updatedToolOutput": value}
        if mcp:
            o["updatedMCPToolOutput"] = value
        return o
    if hits:
        if _dry("post", f"{data.get('tool_name')}: would withhold encoded output"):
            return None
        return {"hookSpecificOutput": output(new),
                "systemMessage": f"cloakroom: withheld encoded {data.get('tool_name')} output{tail}"}
    if img and not (n["ocr"] or n["held"]) and not changed:  # only allow_images passes: nothing replaced
        return {"systemMessage": f"cloakroom: {img}"}
    if not changed and not converted:
        return None
    if _dry("post", f"{data.get('tool_name')}: would mask {_summary(kinds)}{tail}"):
        return None
    out = output(new)
    what = {"pdf": "PDF text", "ocr": "OCR text", "figures": "notebook (figures held back)"}.get(converted, f"{data.get('tool_name')} output")
    if mcp:
        what = f"{data.get('tool_name')} output"
    if not changed:
        return {"hookSpecificOutput": out, "systemMessage": f"cloakroom: checked {what}, nothing to mask{tail}"}
    guide = _guide_once(session)
    if guide:
        out["additionalContext"] = guide
    return {"hookSpecificOutput": out,
            "systemMessage": f"cloakroom: masked {_summary(kinds)} in {what}{tail}"}


def _prime(command: str, cwd: str, session: str, powershell: bool = False):
    """Scan files a command names before it runs, so the vault knows their values and transformed output
    (od, head -c, cut, sed...) can be recognised afterwards. Cached by path, size and mtime."""
    import shlex
    try:
        # posix=False keeps Windows backslashes; strip the quotes it leaves on
        words = [w.strip("\"'") for w in shlex.split(command, posix=not (powershell or os.name == "nt"))]
    except ValueError:
        words = command.split()
    seen = config.private_dir("primed") / f"{''.join(c for c in session if c.isalnum() or c in '-_')[:80]}.json"
    cache = json.loads(seen.read_text()) if seen.exists() else {}
    for w in words:
        path = os.path.join(cwd, os.path.expanduser(w))
        if w.startswith("-") or not os.path.isfile(path):
            continue
        st = os.stat(path)
        key = f"{os.path.realpath(path)}:{st.st_size}:{st.st_mtime_ns}"
        if st.st_size > PRIME_MAX_BYTES:
            _skip("too-large", "pre-read of a file a command names",
                  "a file this command names before it ran (its output is still checked)", size=st.st_size,
                  limit=PRIME_MAX_BYTES)
            continue
        if key in cache:
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            client.mask(f.read(), session)
        cache[key] = 1
    fd = os.open(seen, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cache, f)


def _guide_once(session: str) -> str | None:
    flag = config.private_dir("guided") / ("".join(c for c in session if c.isalnum() or c in "-_")[:80] or "default")
    if flag.exists():
        return None
    flag.touch(mode=0o600)
    return GUIDE


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"}
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Move to): (.+?)\s*$", re.M)


def _write_paths(tool: str, args: dict) -> list[str]:
    """The files a write tool changes: one for Claude Code's tools, every file a Codex patch names."""
    if tool == "apply_patch":
        return _PATCH_FILE.findall(str(args.get("command", "")))
    return [str(args.get("file_path") or args.get("notebook_path") or "")]


def _touches_home(tool: str, args: dict) -> bool:
    """A tool call that would change Cloakroom's own files (~/.cloakroom: pause, rules, vault, token)."""
    home = str(config.HOME)
    if tool in WRITE_TOOLS:
        return any(os.path.realpath(os.path.expanduser(p)).startswith(os.path.realpath(home))
                   for p in _write_paths(tool, args))
    if tool in SHELL_TOOLS:
        cmd = str(args.get("command", ""))
        return ".cloakroom" in cmd or home in cmd
    return False


COMMIT_KINDS = {"SECRET", "PASSWORD"}  # kinds kept out of files git would commit; personal data is not checked
SECRET_FILES = (".env*", ".envrc", "*.pem", "*.key", "credentials*", "*.p12")  # where a key belongs
_DRY_DENY = {"dry-run": "would deny"}  # _commit_check in dry run: log only, and skip the restore's own dry line
# > f, >> f, 2> f, | tee [-a] f; not the arrows in -> and =>, nor >=
_REDIRECT = re.compile(r"(?:(?<![-=>\d])\d?>>?(?!=)|\|\s*tee(?:\s+-a)?)\s*([^\s|;&<>=][^\s|;&<>]*)")
# MCP and agent config files a repo may commit: a token there belongs in an environment reference
AGENT_CONFIGS = (".mcp.json", ".claude/settings.json", ".claude/settings.local.json", ".cursor/mcp.json",
                 "claude_desktop_config.json")
_CONFIG_SECRET = re.compile(r'(?i)"(?P<key>[^"\n]*(?:token|key|secret|passw|auth|credential|bearer)[^"\n]*)"\s*:\s*'
                            r'"(?:bearer\s+|token\s+|basic\s+)?(?P<value>[^"\s]*)"')


def _agent_config(path: str) -> bool:
    p = path.replace("\\", "/")
    return any(p == c or p.endswith("/" + c) for c in AGENT_CONFIGS)


def _config_literals(text: str) -> list[str]:
    """Literal tokens under key-, token- or secret-like names in a JSON config (not ${VAR} or env: references)."""
    out = []
    for m in _CONFIG_SECRET.finditer(text):
        v = m.group("value")
        if len(v) < shell.LITERAL_MIN or "$" in v or v.startswith(("/", "~", ".", "env:")) or ANY_PLACEHOLDER.search(v) \
                or not re.fullmatch(r"[A-Za-z0-9_\-.=+/]+", v) or not (re.search(r"\d", v) and re.search(r"[A-Za-z]", v)):
            continue
        out.append(v)
    return out


def _existing(path: str) -> str:
    """The file as it is now, so a key it already holds isn't counted as newly written. "" when absent or large."""
    try:
        if os.path.getsize(path) <= 4 * PRIME_MAX_BYTES:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
    except OSError:
        pass
    return ""


def _written_text(tool: str, args: dict) -> str:
    if tool == "Write":
        return str(args.get("content", ""))
    if tool == "Edit":
        return str(args.get("new_string", ""))
    if tool == "MultiEdit":
        return "\n".join(str(e.get("new_string", "")) for e in args.get("edits") or [] if isinstance(e, dict))
    if tool == "NotebookEdit":
        src = args.get("new_source", "")
        return "".join(src) if isinstance(src, list) else str(src)
    if tool == "apply_patch":  # the lines it adds
        return "\n".join(ln[1:] for ln in str(args.get("command", "")).splitlines() if ln.startswith("+"))
    return ""


def _git_would_commit(path: str) -> bool:
    """The file is in a git repo and not ignored. Any doubt (no git, not a repo, slow) answers False: restore as before."""
    import subprocess
    d = os.path.dirname(path)
    while d and not os.path.isdir(d):
        d = os.path.dirname(d) if os.path.dirname(d) != d else ""
    if not d:
        return False
    try:
        r = subprocess.run(["git", "-C", d, "check-ignore", "-q", "--", path], capture_output=True, timeout=1)
    except subprocess.TimeoutExpired:
        _skip("too-slow", "git check on a write holding a key",
              "whether git would commit this write, which holds a key", ms=1000, limit=1)
        return False
    except Exception:  # no git: nothing can commit the file
        return False
    return r.returncode == 1  # 0 ignored, 1 not ignored, 128 not a repo or error


def _commit_check(tool: str, args: dict, original: dict, cwd: str, vault: Vault) -> dict | None:
    """Deny a local write that would put a key or password into a file git would commit (restored from a
    placeholder, or typed by the model). Writes outside a repo, to ignored files, to .env-style files or to
    allow_secret_paths go through. The reason names the placeholder or kind, never the value."""
    from fnmatch import fnmatch
    cfg = config.load()
    text = ""
    if tool in SHELL_TOOLS:
        cmd = str(original.get("command", ""))
        targets = [t.strip("\"'") for t in _REDIRECT.findall(cmd)]
        found = [(ph, ph.split("_")[1], vault.by_placeholder[ph]) for ph in PLACEHOLDER.findall(cmd)
                 if ph.split("_")[1] in COMMIT_KINDS and ph in vault.by_placeholder]
    else:
        targets = _write_paths(tool, args)
        found = [(ph, ph.split("_")[1], vault.by_placeholder[ph]) for ph in PLACEHOLDER.findall(_written_text(tool, original))
                 if ph.split("_")[1] in COMMIT_KINDS and ph in vault.by_placeholder]
        # the text as it will be written: rules, your always-mask entries and known secrets (plain or encoded)
        text = _written_text(tool, args)
        known = {v: k for v, k in engine.known_values(vault, cfg["always"]).items() if k in COMMIT_KINDS}
        always = [(e, k) for e, k in cfg["always"] if k in COMMIT_KINDS]
        hits, _ = engine.detect(text, "rules", cfg["allow"], always, known=known)
        for f in hits:
            ph = vault.by_value.get(f.value(text), "")
            if f.kind in COMMIT_KINDS and not (ph and any(ph == p for p, _, _ in found)):
                found.append((ph, f.kind, f.value(text)))
    for target in targets:
        if not target:
            continue
        path = os.path.normpath(os.path.join(cwd, os.path.expanduser(target)))
        rel = os.path.relpath(path, cwd)
        rel = path if rel.startswith("..") else rel
        agent = _agent_config(path)
        here = list(found)
        if agent:  # a token under a key-like name, even one the rules don't know
            here += [(vault.by_value.get(v, ""), "SECRET", v) for v in _config_literals(text)
                     if not any(v == x or v in x or x in v for _, _, x in here)]
        if not here:
            continue
        name = os.path.basename(path)
        if any(fnmatch(name, p) for p in SECRET_FILES):
            continue
        if any(fnmatch(rel, g) or fnmatch(path, g) for g in cfg.get("allow_secret_paths") or []):
            continue
        if not _git_would_commit(path):
            continue
        old = _existing(path)  # a key the file already holds is kept, not added: only new ones deny
        here = [x for x in here if not (old and x[2] and x[2] in old)]
        if not here:
            continue
        _FOUND.extend((ph, k) for ph, k, _ in here)
        what = ", ".join(dict.fromkeys(ph or k for ph, k, _ in here))
        if _dry("pre", f"{tool}: would deny a commit-bound write ({_summary([k for _, k, _ in here])})"):
            return _DRY_DENY
        if agent:
            return _deny(f"Cloakroom: this would write {what} into {rel}, an agent config file git would commit. Use "
                         "an environment reference instead (for example \"${GITHUB_TOKEN}\"; Cursor: "
                         "\"${env:GITHUB_TOKEN}\") and set the value in your shell or a git-ignored .env.")
        return _deny(f"Cloakroom: this would write {what} into {rel}, which git would commit. Put the value in "
                     ".env (git-ignored) and read it from the environment.")
    return None


GIT_SECONDS = 2.0  # the commit and push check gives up (lets the command run) after this long
GIT_MAX_TEXT = 4 * 1024 * 1024  # added lines scanned at most; a bigger change set fails open
_COMMIT_VALUE_OPTS = {"-m", "--message", "-F", "--file", "-c", "-C", "--reuse-message", "--reedit-message",
                      "--author", "--date", "-t", "--template", "--fixup", "--squash", "--cleanup", "--trailer"}
_PUSH_VALUE_OPTS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec", "--signed"}


def _git_bash_path(p: str, powershell: bool) -> str:
    """Git Bash on Windows writes C:\\Users\\x as /c/Users/x; git.exe and Python need the drive form."""
    m = None if powershell or sys.platform != "win32" else re.fullmatch(r"/([A-Za-z])(/.*)?", p)
    return f"{m.group(1).upper()}:" + (m.group(2) or "/").replace("/", "\\") if m else p


def _git_steps(command: str, cwd: str, powershell: bool) -> list[tuple[str, str, list[str], bool]]:
    """(commit|push, repo folder, its arguments, a git add ran before it) for each git commit or push in the
    command, following cd and git -C."""
    import shlex
    out, here, added = [], cwd, False
    for seg in (shell._PS_SEGMENT_SPLIT if powershell else shell._SEGMENT_SPLIT).split(command):
        try:
            words = shlex.split(seg, posix=not powershell)
        except ValueError:
            words = seg.split()
        words = [w.strip("\"'") for w in words]
        while words and (re.fullmatch(r"\w+=.*", words[0]) or words[0] in ("sudo", "env", "command", "exec")):
            words = words[1:]
        if not words:
            continue
        if words[0].lower() in ("cd", "pushd", "set-location", "sl") and len(words) > 1:
            here = os.path.join(here, os.path.expanduser(_git_bash_path(words[-1], powershell)))
            continue
        if re.split(r"[\\/]", words[0])[-1].lower() not in ("git", "git.exe"):
            continue
        i, repo = 1, here
        while i < len(words) and words[i].startswith("-"):
            if words[i] == "-C" and i + 1 < len(words):
                repo = os.path.join(repo, os.path.expanduser(_git_bash_path(words[i + 1], powershell)))
                i += 1
            elif words[i] == "-c":
                i += 1
            i += 1
        sub = words[i] if i < len(words) else ""
        if sub == "add":
            added = True
        elif sub in ("commit", "push"):
            out.append((sub, repo, words[i + 1:], added))
    return out


def _commit_diffs(args: list[str], added: bool) -> list[list[str]]:
    """git commands whose output is what a commit with these arguments would record (a superset is fine)."""
    widen, i = added, 0
    while i < len(args):
        a = args[i]
        if a in _COMMIT_VALUE_OPTS:
            i += 1
        elif a in ("-a", "--all", "--include", "-i", "--only", "-o"):
            widen = True
        elif a.startswith("-") and not a.startswith("--"):
            for j, c in enumerate(a[1:]):
                if c == "a":
                    widen = True
                if c in "mFcCt":  # the rest of the cluster (or the next word) is that option's value
                    if j == len(a) - 2:
                        i += 1
                    break
        elif a == "--":
            widen = widen or i + 1 < len(args)
            break
        elif not a.startswith("-"):
            widen = True  # git commit <paths>
        i += 1
    cmds = [["diff", "--cached"]]
    if widen:
        cmds.append(["diff", "HEAD"])
    if added:
        cmds.append(["untracked"])
    return cmds


def _push_logs(args: list[str]) -> list[list[str]]:
    srcs, positional, everything, i = [], [], False, 0
    while i < len(args):
        a = args[i]
        if a in _PUSH_VALUE_OPTS:
            i += 1
        elif a in ("--all", "--mirror", "--branches"):
            everything = True
        elif not a.startswith("-"):
            positional.append(a)
        i += 1
    for ref in positional[1:]:
        src = ref.lstrip("+").split(":")[0]
        if src:
            srcs.append(src)
    srcs = ["--branches"] if everything else srcs or ["HEAD"]
    return [["log", "-p", "--format=", *srcs, "--not", "--remotes"]]


def _added_lines(diff: str) -> dict[str, list[str]]:
    """{file: added lines} from git diff or git log -p output."""
    out: dict[str, list[str]] = {}
    current, header = "", False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            header, current = True, ""
            continue
        if header:
            if line.startswith("+++ "):
                p = line[4:].strip().strip('"')
                current = p[2:] if p.startswith("b/") else ("" if p == "/dev/null" else p)
            elif line.startswith("@@"):
                header = False
            continue
        if current and line.startswith("+"):
            out.setdefault(current, []).append(line[1:])
    return out


def _in_repo(folder: str) -> bool:
    import subprocess
    try:
        return subprocess.run(["git", "-C", folder, "rev-parse", "--git-dir"], capture_output=True,
                              timeout=1).returncode == 0
    except Exception:
        return False


def _git_out(repo: str, deadline: float, *argv) -> str:
    import subprocess
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError
    r = subprocess.run(["git", "-C", repo, "-c", "core.quotePath=false", *argv], capture_output=True, timeout=left)
    if r.returncode != 0:
        raise RuntimeError("git")
    return r.stdout.decode("utf-8", "replace")


def git_added(repo: str, argvs: list[list[str]], vault: Vault | None, cfg: dict,
              deadline: float, check: str = "git commit") -> list[tuple[str, str, str]] | None:
    """(file, placeholder or "", kind) for each key or password the added lines carry (the rules, your always-mask
    entries and the session's known secrets). argvs: git commands (_commit_diffs, _push_logs). None when the change
    set is too large to scan (fail open, a "too-large" skip is noted for check); running out of time raises
    TimeoutError and a git error raises. Also the git hook's check (githook.py)."""
    from fnmatch import fnmatch
    known = {v: k for v, k in engine.known_values(vault, cfg["always"]).items() if k in COMMIT_KINDS}
    always = [(e, k) for e, k in cfg["always"] if k in COMMIT_KINDS]
    globs = cfg.get("allow_secret_paths") or []
    files: dict[str, list[str]] = {}
    for argv in argvs:
        if argv == ["untracked"]:
            root = _git_out(repo, deadline, "rev-parse", "--show-toplevel").strip()
            for name in _git_out(repo, deadline, "ls-files", "--full-name", "--others", "--exclude-standard", "-z",
                                 ":/").split("\0"):
                path = os.path.join(root, name)
                if not (name and os.path.isfile(path)):
                    continue
                if os.path.getsize(path) > 4 * PRIME_MAX_BYTES:
                    _skip("too-large", "new file in a git commit", "a new file in this git commit",
                          size=os.path.getsize(path), limit=4 * PRIME_MAX_BYTES)
                    continue
                with open(path, encoding="utf-8", errors="replace") as f:
                    files.setdefault(name, []).extend(f.read().splitlines())
            continue
        if argv[:2] == ["diff", "HEAD"]:
            try:
                out = _git_out(repo, deadline, *argv, "--no-color", "--no-ext-diff", "-U0")
            except RuntimeError:  # no commit yet: the staged diff covers it
                continue
        else:
            out = _git_out(repo, deadline, *argv, "--no-color", "--no-ext-diff", "-U0")
        for name, lines in _added_lines(out).items():
            files.setdefault(name, []).extend(lines)
    size = sum(len(x) for lines in files.values() for x in lines)
    if size > GIT_MAX_TEXT:
        _skip("too-large", check, size=size, limit=GIT_MAX_TEXT)
        return None
    found = []
    for name, lines in files.items():
        if time.monotonic() > deadline:
            raise TimeoutError
        if any(fnmatch(name, g) or fnmatch(os.path.basename(name), g) for g in globs):
            continue
        text = "\n".join(lines)
        hits, _ = engine.detect(text, "rules", cfg["allow"], always, known=known)
        for f in hits:
            if f.kind in COMMIT_KINDS:
                found.append((name, vault.by_value.get(f.value(text), "") if vault else "", f.kind))
    return found


def _git_hold(command: str, cwd: str, vault: Vault, powershell: bool) -> dict | None:
    """Hold a git commit or push whose changes carry a known secret or a key the rules recognise, like the egress
    hold (cr: allow lets it run once). Names the file and placeholder or kind, never the value. Fails open on any
    git error, on a change set too large to scan, or after GIT_SECONDS."""
    import subprocess
    steps = _git_steps(command, cwd, powershell)
    if not steps:
        return None
    deadline = time.monotonic() + GIT_SECONDS
    cfg = config.load()
    found, verb, sub, repo = [], "", steps[0][0], steps[0][1]
    try:
        for sub, repo, args, added in steps:
            argvs = _commit_diffs(args, added) if sub == "commit" else _push_logs(args)
            found = git_added(repo, argvs, vault, cfg, deadline, f"git {sub}")
            if found is None:
                return None
            if found:
                verb = sub
                break
    except (TimeoutError, subprocess.TimeoutExpired):  # slow: the command runs as before, unchecked
        _skip("too-slow", f"git {sub}", ms=1000 * (time.monotonic() - deadline + GIT_SECONDS), limit=GIT_SECONDS)
        return None
    except Exception:  # git missing or failing, not a repo: the command runs as before, unchecked
        if _in_repo(repo):  # no git, or no repo there: the command can't commit or push either
            _skip("git-error", f"git {sub}")
        return None
    if not found:
        return None
    _FOUND.extend((ph, k) for _, ph, k in found)
    if _allowed_once(command):
        _ACTION[0] = "allowed once"
        return None
    pairs = list(dict.fromkeys(f"{ph or 'a ' + k} in {name}" for name, ph, k in found))
    what = "; ".join(pairs[:3]) + (f" (and {len(pairs) - 3} more)" if len(pairs) > 3 else "")
    if _dry("pre", f"git {verb}: would hold ({_summary([k for _, _, k in found])})"):
        return None
    _write_held({"mac": _command_mac(command), "until": time.time() + 60 * HOLD_MINUTES, "armed": False})
    _ACTION[0] = "held"
    return _deny(f"Cloakroom held this git {verb}: {what}. A pushed secret can't be called back. Move the value to "
                 f".env (git-ignored) and unstage it, or, to {verb} anyway, type cr: allow, then ask Claude to run the "
                 f"same command again: it passes once, within {HOLD_MINUTES} min. Claude: don't change or re-encode the "
                 "value to get past this; ask the user.")


GUESS_NOTICE = 3  # refused placeholders in one session before the user is told


def _refusals(vault: Vault, session: str) -> tuple[str, str]:
    """What to tell Claude and the user about placeholders left as text (vault.refused). Counts the refusals that
    aren't upgrade leftovers per session; the user hears once, when they reach GUESS_NOTICE. Never values."""
    if not vault.refused:
        return "", ""
    old = sorted({p for p, why in vault.refused if why == OLD_FORMAT})
    other = sorted({p for p, why in vault.refused if why != OLD_FORMAT})
    _EXTRA["refused"] = events.counts([why for _, why in vault.refused])
    context, notice = [], []
    if old:
        context.append(f"{', '.join(old)}: from before Cloakroom 0.6.1, so not restored (left as text). Ask the user "
                       "for the value again; it will get a new placeholder.")
        notice.append(f"left {len(old)} placeholder{'s' if len(old) > 1 else ''} from before 0.6.1 as text")
    if other:
        context.append(f"Placeholders Cloakroom didn't issue in this session (left as text): {', '.join(other)}")
        counter = config.private_dir("refused") / config.vault_path(session).stem
        try:
            before = int(counter.read_text())
        except (OSError, ValueError):
            before = 0
        after = before + len(other)
        fd = os.open(counter, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(str(after))
        if before < GUESS_NOTICE <= after:
            notice.append(f"{after} placeholder guesses refused this session")
    return " ".join(context), ("cloakroom: " + "; ".join(notice)) if notice else ""


def _with_notes(out: dict, context: str, notice: str) -> dict:
    if context:
        out["additionalContext"] = context
    return {"hookSpecificOutput": out, **({"systemMessage": notice} if notice else {})}


def _command_paths(command: str, cwd: str, powershell: bool) -> list[str]:
    """Paths a shell command names (arguments and redirect targets) that may receive restored values."""
    import shlex
    try:
        words = [w.strip("\"'") for w in shlex.split(command, posix=not (powershell or os.name == "nt"))]
    except ValueError:
        words = command.split()
    words += [t.strip("\"'") for t in _REDIRECT.findall(command)]
    out = []
    for w in words:
        if w and not w.startswith("-") and not ANY_PLACEHOLDER.search(w) and re.search(r"[\w.]", w):
            out.append(filelog.norm(w, cwd))
    return list(dict.fromkeys(out))[:50]


def _pending(data: dict, paths: list[str], text: str, vault: Vault):
    """A restoring write is about to run: note its paths and the placeholders it restores, for the file log."""
    restored = [p for p in PLACEHOLDER.findall(text) if p in vault.by_placeholder]
    if data.get("tool_use_id") and paths and restored:
        filelog.pending(str(data["tool_use_id"]), paths, restored)


def on_pre(data: dict) -> dict | None:
    tool, args, session = data.get("tool_name", ""), data.get("tool_input") or {}, data.get("session_id", "default")
    if _touches_home(tool, args):
        return _deny("Cloakroom: its own settings (~/.cloakroom) are changed only by you, from the dashboard or "
                     "by typing cr: pause / cr: resume. Claude can't change them.")
    blob = json.dumps(args)
    has_ph = bool(ANY_PLACEHOLDER.search(blob))
    vault = Vault(config.vault_path(session))
    cwd = data.get("cwd") or os.getcwd()

    money_why = money.reason(tool, args, SHELL_TOOLS, config.load())
    if tool in SHELL_TOOLS:
        held = _egress_hold(args.get("command", ""), session, vault, tool == "PowerShell", money_why)
        if held:
            return held
        held = _git_hold(args.get("command", ""), cwd, vault, tool == "PowerShell")
        if held:
            return held
        if data.get("_cloakroom_agent") == "codex" and config.load().get("codex_hold_long_running"):
            held = _long_hold(args.get("command", ""))
            if held:
                return held
        _prime(args.get("command", ""), cwd, session, powershell=tool == "PowerShell")
    elif money_why:
        held = _money_hold(tool + "\0" + json.dumps(args, sort_keys=True), money_why)
        if held:
            return held
    if tool in LOCAL_FILE_TOOLS or tool in SHELL_TOOLS:
        if not has_ph:  # nothing to restore; a key the model typed into a file is still checked
            denied = _commit_check(tool, args, args, cwd, vault) if tool in WRITE_TOOLS else None
            return None if denied is _DRY_DENY else denied
        if tool in SHELL_TOOLS:
            ps = tool == "PowerShell"
            cmd = args.get("command", "")
            kind = shell.classify(cmd, powershell=ps)
            if kind == "network" or "<<" in cmd or "@'" in cmd or '@"' in cmd:
                out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                       "permissionDecisionReason": "Cloakroom: this command can send data off this machine, so "
                                                   "placeholders were left in place. Approve only if that's what you want."}}
                return None if _dry("pre", "Bash network: would ask") else out
            restored, _ = shell.restore(cmd, vault.lookup, powershell=ps)  # refusals: vault.refused
            denied = _commit_check(tool, args, args, cwd, vault)
            if denied:
                return None if denied is _DRY_DENY else denied
            if _dry("pre", f"Bash {kind}: would restore"):
                return None
            context, notice = _refusals(vault, session)
            if restored == cmd:
                return _with_notes({"hookEventName": "PreToolUse"}, context, notice) if context else None
            _pending(data, _command_paths(cmd, cwd, ps), cmd, vault)
            out = {"hookEventName": "PreToolUse", "updatedInput": {**args, "command": restored}}
            if kind == "unknown":
                out.update(permissionDecision="ask",
                           permissionDecisionReason="Cloakroom restored real values into a command it doesn't know. "
                                                    "Approve only if it stays on this machine.")
            return _with_notes(out, context, notice)
        changed_fields = {}
        for key, value in args.items():
            def fn(s):
                return vault.restore(s)[0]  # refusals: vault.refused
            new, changed = _strings(value, fn)
            if changed:
                changed_fields[key] = new
        if tool in WRITE_TOOLS:
            denied = _commit_check(tool, {**args, **changed_fields}, args, cwd, vault)
            if denied:
                return None if denied is _DRY_DENY else denied
        if changed_fields and _dry("pre", f"{tool}: would restore {list(changed_fields)}"):
            return None
        context, notice = _refusals(vault, session)
        if not changed_fields:
            return _with_notes({"hookEventName": "PreToolUse"}, context, notice) if context else None
        if tool in WRITE_TOOLS:
            _pending(data, [filelog.norm(p, cwd) for p in _write_paths(tool, args) if p], json.dumps(args), vault)
        # updatedInput replaces the whole input in practice (a partial one dropped Write's file_path): send it all
        out = {"hookEventName": "PreToolUse", "updatedInput": {**args, **changed_fields}}
        return _with_notes(out, context, notice)

    # Everything else may leave the machine (web, MCP, subagent prompts): never restore; deny real values.
    r = client.mask(blob, session, roots=[cwd])
    if r["masked"]:
        _FOUND.extend((p, k) for p, k, _ in r["masked"])
        kinds = _summary([k for _, k, _ in r["masked"]])
        return None if _dry("pre", f"{tool}: would deny ({kinds})") else _deny(
            f"Cloakroom: {tool} would send {kinds} off this machine. Use the placeholders instead of real values.")
    return None


def _install_note(cfg: dict) -> str | None:
    if cfg.get("installing") in ("rules", "model"):
        return ("cloakroom: rules-only protection while the model downloads (one time, a few minutes). Keys, cards "
                "and IDs are covered; names and addresses may be missed until it's done. Progress: "
                "~/.cloakroom/bootstrap.log" + (" " + crypto.PREWARN if sys.platform == "darwin" else ""))
    if cfg.get("installing") == "model-failed":
        import platform
        from . import __version__
        cmd = f"uv tool install --force --python 3.12 'cloakroom[model,torch]=={__version__}'"
        win = sys.platform == "win32"
        state = "%USERPROFILE%\\.cloakroom\\bootstrap.state" if win else "~/.cloakroom/bootstrap.state"
        log = "%USERPROFILE%\\.cloakroom\\bootstrap.log" if win else "~/.cloakroom/bootstrap.log"
        steps = ["Cloakroom's model install failed. Rules-only protection is on: keys, cards and IDs are covered, "
                 "names and addresses are not yet.",
                 f"To retry: delete {state} and restart Claude Code (/exit, then claude), or run: {cmd}",
                 f"Log: {log}"]
        if sys.platform == "darwin" and platform.machine() == "x86_64":
            steps.append("Intel Macs stay rules-only by design: PyTorch and ONNX Runtime have no Intel Mac builds.")
        return "\n".join(f"{i}. {t}" for i, t in enumerate(steps, 1))
    return None


def _install_note_once(session: str) -> str | None:
    """The install note again on the session's first prompt: Claude Code may show only one plugin's SessionStart
    message, so ours can be hidden behind another's."""
    note = _install_note(config.load())
    if not note:
        return None
    flag = config.private_dir("install-noted") / ("".join(c for c in session if c.isalnum() or c in "-_")[:80]
                                                  or "default")
    if flag.exists():
        return None
    flag.touch(mode=0o600)
    return note


def on_start(data: dict) -> dict | None:
    """Session start: a log line (version, mode, hooks installed), then warm the service so the first tool call
    isn't blocked while the model loads."""
    cfg = config.load()
    _ACTION[0] = "session-start"
    _EXTRA.update(version=_version(), mode=cfg["mode"], hooks=_hooks_installed())
    note = _install_note(cfg) or crypto.denial_notice()
    if note:
        return {"systemMessage": note}
    if cfg["mode"] == "full":
        try:
            # wait=0: start the service and return at once (the SessionStart hook only has 30 s; the first model
            # download alone can take longer). Tool calls wait for it instead.
            client._post("/mask", {"text": "warm up", "session": data.get("session_id", "default")}, timeout=5, wait=0)
        except client.Unavailable:
            return {"systemMessage": "cloakroom: starting the local model in the background. The very first run also "
                                     "downloads it once (about a minute); tool calls wait for it."}
    return None


def _version() -> str:
    from . import __version__
    return __version__


def _hooks_installed() -> list[str]:
    """The hook events the plugin registers (its hooks.json), [] when not run from the plugin."""
    try:
        root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
        with open(os.path.join(root, "hooks", "hooks.json"), encoding="utf-8") as f:
            return sorted(json.load(f)["hooks"])
    except Exception:
        return []


HANDLERS = {"prompt": on_prompt, "post": on_post, "pre": on_pre, "start": on_start}


def fail_closed(event: str, why: str, data=None) -> dict:
    _ACTION[0] = "fail-closed"
    return closed(event, why, data)


def _prompt_rules_only(data: dict, why: str) -> dict:
    """The service can't check this prompt: run the rules in-process (no model, no service) so a pasted key or card
    is still blocked, and otherwise let the prompt through with a warning instead of locking the user out."""
    try:
        cfg = config.load()
        r = engine.mask(data.get("prompt", ""), mode="rules", allow=cfg["allow"], always=cfg["always"])
    except Exception:
        return fail_closed("prompt", why, data)
    if r.masked:
        _FOUND.extend((p, k) for p, k, _ in r.masked)
        kinds = _summary([k for _, k, _ in r.masked])
        return {"decision": "block", "reason": f"Cloakroom blocked this prompt: it contains {kinds}. {TIP}"}
    _skip("model", "model check", say=False)  # the warning below already says so, on every such prompt
    return fail_closed("prompt", f"{why}; checked with rules only", data)


def _model_fallback(event: str, data: dict):
    """This call runs on the rules although the model is set: Gemini's cold start (agents._rules_until_warm, told
    once per session) or the first-start model download (told by the install note). Only calls that run detection."""
    from .agents import NOTE
    tool = data.get("tool_name", "")
    if event == "start" or (event == "pre" and (tool in LOCAL_FILE_TOOLS or tool in SHELL_TOOLS)):
        return
    if data.get(NOTE):
        _skip("model", "model check", "this with the local model")
    elif config.load().get("installing") in ("rules", "model"):
        _skip("model", "model check", say=False)


def handle(event: str, data: dict) -> dict | None:
    _FOUND.clear()
    _EXTRA.clear()
    _SKIPS.clear()
    _ACTION[0] = _NOTE[0] = ""
    t0 = time.perf_counter()
    try:
        _model_fallback(event, data)
        out = HANDLERS[event](data)
    except (client.Unavailable, engine.ModelUnavailable) as e:
        _ACTION[0] = ""
        out = _prompt_rules_only(data, str(e)) if event == "prompt" else fail_closed(event, str(e), data)
    except Exception as e:
        out = fail_closed(event, type(e).__name__, data)
    try:
        said = _skip_message(str(data.get("session_id", "default"))) if _SKIPS else ""
    except Exception:
        said = ""
    if said:
        out = dict(out or {})
        out["systemMessage"] = said + (" · " + out["systemMessage"] if out.get("systemMessage") else "")
    _log(event, data, out, (time.perf_counter() - t0) * 1000)
    return out


def main(event: str) -> int:
    """Kept for tests: read stdin, print the answer."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
        out = fail_closed(event, "bad input", data)
    else:
        out = handle(event, data)
    if out:
        print(json.dumps(out))
    return 0


def _log(event: str, data, out, ms: float = 0.0):
    """One line per hook call: event, tool, decision (hook.log), plus the dashboard's event (events.py).
    Never values or text."""
    try:
        h = (out or {}).get("hookSpecificOutput", {})
        decision = _ACTION[0] or (out or {}).get("decision") or h.get("permissionDecision") or (
            "withheld" if "withheld" in str(h.get("updatedToolOutput", "")) else
            "masked" if "updatedToolOutput" in h else "restored" if "updatedInput" in h else "pass")
        tool = data.get("tool_name", "-") if isinstance(data, dict) else "-"
        with open(config.private_dir() / "hook.log", "a") as f:
            note = f" {_NOTE[0]}" if _NOTE[0] else ""
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {event} {tool} {decision}{note}\n")
        target = events.target(tool, data.get("tool_input")) if isinstance(data, dict) and _FOUND else ""
        if target:  # a file name can hold a name or an email too: log it masked, like the output
            home = os.path.expanduser("~").rstrip("/\\")
            if home and target.startswith((home + os.sep, home + "/")):  # Windows paths can use either slash
                target = "~" + target[len(home):]  # the home folder's name is not logged, so the report's path class survives masking
            try:
                target = client.mask(target, data.get("session_id", "default"))["text"]
            except Exception:
                target = "[path not checked]"
        events.record(event, data, decision, _FOUND, ms, target, dict(_EXTRA))
        for reason, check, _, _, numbers in _SKIPS:
            numbers = dict(numbers)
            events.record("skipped", data, "skipped", [], numbers.pop("ms"), "", {"reason": reason, "check": check, **numbers})
    except Exception:
        pass
