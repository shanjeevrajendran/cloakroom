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

from . import client, config, decode, engine, events, media, shell
from .hookentry import STRUCTURAL, _withhold, closed
from .vault import PLACEHOLDER, Vault

SHELL_TOOLS = {"Bash", "PowerShell"}
LOCAL_FILE_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "NotebookRead", "Glob", "Grep", "LS"}
GUIDE = ("Cloakroom note: values like PII_EMAIL_1 are placeholders for real data kept on this machine. Use them "
         "verbatim in Write, Grep and local Bash commands, and in Edit's new_string: Cloakroom restores the real "
         "values before the tool runs and masks them again in the output. Edit's old_string must not contain a "
         "placeholder (the file holds the real value, so it won't match): anchor old_string on nearby text without "
         "placeholders, or rewrite the file with Write. Don't try to recover the real values.")
PRIME_MAX_BYTES = 512 * 1024
_ACTION = [""]  # set when a prompt passes by your own choice (sent anyway, paused), for the event log
_NOTE = [""]  # extra detail for the hook.log line (OCR pages and time), never values
_FOUND: list[tuple[str, str]] = []  # (placeholder, kind) found during the current hook call, for the event log
TIP = ("Cloakroom can't rewrite a prompt, only block it. Put the detail in a file and ask Claude to read it: "
       "it will be masked on the way in and restored for local edits. Send it anyway: add cr! at the start. "
       "Pause prompt checks: type cr: pause 10m (tool output stays masked). Resume: cr: resume.")
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


def _summary(kinds: list[str]) -> str:
    counts = {}
    for k in kinds:
        counts[k] = counts.get(k, 0) + 1
    return ", ".join(f"{k} x{n}" for k, n in sorted(counts.items()))


def _dry(event: str, note: str) -> bool:
    if not os.environ.get("CLOAKROOM_DRY_RUN"):
        return False
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
    m = _PAUSE_CMD.match(prompt)
    if not m:
        return None
    if m.group(1).lower() == "resume":
        _pause_file().unlink(missing_ok=True)
        return {"decision": "block", "reason": "Cloakroom: prompt checks are back on."}
    minutes = min(int(m.group(2) or 10), 240)
    fd = os.open(_pause_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"until": time.time() + 60 * minutes}, f)
    return {"decision": "block", "reason": f"Cloakroom: prompt checks paused for {minutes} min. Tool output is still "
                                           "masked. Turn checks back on early: cr: resume."}


def on_prompt(data: dict) -> dict | None:
    out = _on_prompt(data)
    note = _install_note_once(data.get("session_id", "default"))
    if note:
        out = dict(out or {})
        out["systemMessage"] = note + (" · " + out["systemMessage"] if out.get("systemMessage") else "")
    return out


def _on_prompt(data: dict) -> dict | None:
    prompt = data.get("prompt", "")
    control = _control(prompt)
    if control:
        _ACTION[0] = "control"
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

    def fn(s):
        if len(s) < 4:
            return s
        r = client.mask(s, session, ocr=converted == "ocr")
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
            if decode.known_value_hit(view, known) or client.mask(view, session)["masked"]:
                hit = True
                break
        if hit:
            hits.append(1)
            return msg
        return t
    new, _ = _strings(new, check)
    if hits:
        if _dry("post", f"{data.get('tool_name')}: would withhold encoded output"):
            return None
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": new},
                "systemMessage": f"cloakroom: withheld encoded {data.get('tool_name')} output"}
    if not changed and not converted:
        return None
    if _dry("post", f"{data.get('tool_name')}: would mask {_summary(kinds)}"):
        return None
    out = {"hookEventName": "PostToolUse", "updatedToolOutput": new}
    what = {"pdf": "PDF text", "ocr": "OCR text", "figures": "notebook (figures held back)"}.get(converted, f"{data.get('tool_name')} output")
    if not changed:
        return {"hookSpecificOutput": out, "systemMessage": f"cloakroom: checked {what}, nothing to mask"}
    guide = _guide_once(session)
    if guide:
        out["additionalContext"] = guide
    return {"hookSpecificOutput": out,
            "systemMessage": f"cloakroom: masked {_summary(kinds)} in {what}"}


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
        if st.st_size > PRIME_MAX_BYTES or key in cache:
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


WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _touches_home(tool: str, args: dict) -> bool:
    """A tool call that would change Cloakroom's own files (~/.cloakroom: pause, rules, vault, token)."""
    home = str(config.HOME)
    if tool in WRITE_TOOLS:
        path = str(args.get("file_path") or args.get("notebook_path") or "")
        return os.path.realpath(os.path.expanduser(path)).startswith(os.path.realpath(home))
    if tool in SHELL_TOOLS:
        cmd = str(args.get("command", ""))
        return ".cloakroom" in cmd or home in cmd
    return False


def on_pre(data: dict) -> dict | None:
    tool, args, session = data.get("tool_name", ""), data.get("tool_input") or {}, data.get("session_id", "default")
    if _touches_home(tool, args):
        return _deny("Cloakroom: its own settings (~/.cloakroom) are changed only by you, from the dashboard or "
                     "by typing cr: pause / cr: resume. Claude can't change them.")
    blob = json.dumps(args)
    has_ph = bool(PLACEHOLDER.search(blob))
    vault = Vault(config.vault_path(session))

    if tool in SHELL_TOOLS:
        _prime(args.get("command", ""), data.get("cwd") or os.getcwd(), session, powershell=tool == "PowerShell")
    if tool in LOCAL_FILE_TOOLS or tool in SHELL_TOOLS:
        if not has_ph:
            return None
        if tool in SHELL_TOOLS:
            ps = tool == "PowerShell"
            cmd = args.get("command", "")
            kind = shell.classify(cmd, powershell=ps)
            if kind == "network" or "<<" in cmd or "@'" in cmd or '@"' in cmd:
                out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                       "permissionDecisionReason": "Cloakroom: this command can send data off this machine, so "
                                                   "placeholders were left in place. Approve only if that's what you want."}}
                return None if _dry("pre", "Bash network: would ask") else out
            restored, unknown = shell.restore(cmd, vault.by_placeholder.get, powershell=ps)
            if _dry("pre", f"Bash {kind}: would restore"):
                return None
            out = {"hookEventName": "PreToolUse", "updatedInput": {**args, "command": restored}}
            if kind == "unknown":
                out.update(permissionDecision="ask",
                           permissionDecisionReason="Cloakroom restored real values into a command it doesn't know. "
                                                    "Approve only if it stays on this machine.")
            if unknown:
                out["additionalContext"] = f"Placeholders not in the vault (left as is): {', '.join(sorted(set(unknown)))}"
            return {"hookSpecificOutput": out}
        changed_fields, unknown = {}, []
        for key, value in args.items():
            def fn(s):
                text, unk = vault.restore(s)
                unknown.extend(unk)
                return text
            new, changed = _strings(value, fn)
            if changed:
                changed_fields[key] = new
        if not changed_fields or _dry("pre", f"{tool}: would restore {list(changed_fields)}"):
            return None
        # updatedInput replaces the whole input in practice (a partial one dropped Write's file_path): send it all
        out = {"hookEventName": "PreToolUse", "updatedInput": {**args, **changed_fields}}
        if unknown:
            out["additionalContext"] = f"Placeholders not in the vault (left as is): {', '.join(sorted(set(unknown)))}"
        return {"hookSpecificOutput": out}

    # Everything else may leave the machine (web, MCP, subagent prompts): never restore; deny real values.
    r = client.mask(blob, session)
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
                "~/.cloakroom/bootstrap.log")
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
    """Session start: warm the service so the first tool call isn't blocked while the model loads."""
    cfg = config.load()
    note = _install_note(cfg)
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


HANDLERS = {"prompt": on_prompt, "post": on_post, "pre": on_pre, "start": on_start}


def fail_closed(event: str, why: str, data=None) -> dict:
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
    return fail_closed("prompt", f"{why}; checked with rules only", data)


def handle(event: str, data: dict) -> dict | None:
    _FOUND.clear()
    _ACTION[0] = _NOTE[0] = ""
    t0 = time.perf_counter()
    try:
        out = HANDLERS[event](data)
    except (client.Unavailable, engine.ModelUnavailable) as e:
        out = _prompt_rules_only(data, str(e)) if event == "prompt" else fail_closed(event, str(e), data)
    except Exception as e:
        out = fail_closed(event, type(e).__name__, data)
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
            try:
                target = client.mask(target, data.get("session_id", "default"))["text"]
            except Exception:
                target = "[path not checked]"
        events.record(event, data, decision, _FOUND, ms, target)
    except Exception:
        pass
