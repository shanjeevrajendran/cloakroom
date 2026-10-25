"""cloakroom CLI. Never prints real values except `restore`, which is for you, not the agent.

  cloakroom check [FILE|-] [--mode full|rules]   what would be masked, and what was skipped as safe (no values)
  cloakroom mask  [FILE|-] [--mode ...]          print the masked text
  cloakroom explain [FILE|-]                     every finding with its rule and reason
  cloakroom serve                                run the local service (keeps the model loaded)
  cloakroom status                               mode, service health, vault sessions
  cloakroom dashboard                            open the local dashboard in your browser, signed in
  cloakroom report [--since DATE] [--format csv|json] [--verify]   counts from the event log, no values
  cloakroom clean-history [--dry-run]            find and replace keys in old Claude Code transcripts
  cloakroom install git-hook                     check your own commits and pushes in this repo for keys
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from . import __version__, config, engine


def _read(path: str) -> str:
    text = sys.stdin.read() if path in ("-", None) else open(path, encoding="utf-8-sig", errors="replace").read()
    return text.lstrip("\ufeff")  # a byte-order mark (PowerShell 5.1 writes one) is not content


def _utf8_stdio():
    """Windows consoles default to a legacy code page (cp1252) that can't print every character; the CLI always
    speaks UTF-8 so masked output with any script (names in Chinese, Arabic, ...) never crashes it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower().replace("-", "") != "utf8":
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv=None):
    _utf8_stdio()
    p = argparse.ArgumentParser(prog="cloakroom", description="Agents check their secrets at the door.")
    p.add_argument("--version", action="version", version=f"cloakroom {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("check", "mask", "explain"):
        s = sub.add_parser(name)
        s.add_argument("file", nargs="?", default="-")
        s.add_argument("--mode", choices=["full", "rules"])
    h = sub.add_parser("hook", help="Claude Code hook entry point (reads hook JSON on stdin)")
    h.add_argument("event", choices=["start", "prompt", "pre", "post"])
    h.add_argument("--agent", choices=["claude", "codex", "gemini"], default="claude",
                   help="whose hook JSON (default: Claude Code)")
    for name, what in (("install", "add Cloakroom's hooks to"), ("uninstall", "remove Cloakroom's hooks from")):
        sub.add_parser(name, help=f"{what} another coding agent (Codex, Gemini CLI), or this repo's git hooks "
                                  "(git-hook)").add_argument("agent", choices=["codex", "gemini", "git-hook"])
    g = sub.add_parser("git-check", help="the git hook's check (cloakroom install git-hook): keys and passwords a "
                                         "commit or push adds")
    g.add_argument("kind", choices=["commit", "push"])
    dr = sub.add_parser("doctor", help="check this machine: Python, vault key store, model, service")
    dr.add_argument("--retry-key", action="store_true", help="forget a refused vault-key read and try the key store again")
    x = sub.add_parser("export-onnx", help="export the model to ONNX for fast CPU inference (needs cloakroom[torch] once)")
    x.add_argument("--out", default=None, help="directory (default: the Cloakroom home)")
    x.add_argument("--if-no-gpu", action="store_true",
                   help="skip on an Apple Silicon or NVIDIA GPU, where the model already runs fast (used by the installer)")
    sub.add_parser("serve")
    sub.add_parser("status")
    d = sub.add_parser("dashboard", help="open the local dashboard in your browser (alerts, labels)")
    d.add_argument("--print", action="store_true", help="print the one-time link instead of opening a browser")
    r = sub.add_parser("report", help="counts per day and session from the event log (no values); --verify checks it")
    r.add_argument("--since", default="", help="first day, YYYY-MM-DD")
    r.add_argument("--format", choices=["csv", "json"], default="csv")
    r.add_argument("--verify", action="store_true", help="check the log's hash chain and print the head hash")
    c = sub.add_parser("clean-history", help="find keys and passwords in old Claude Code transcripts and replace them")
    c.add_argument("--dry-run", action="store_true", help="count only, change nothing (always so on the first run)")
    c.add_argument("--yes", action="store_true", help="don't ask before replacing")
    a = p.parse_args(argv)

    if a.cmd == "report":
        from . import report
        if a.verify:
            print(report.verify_text())
            from . import events
            return 0 if events.verify()["ok"] else 1
        sys.stdout.write(report.export(a.format, a.since))
        return 0
    if a.cmd == "clean-history":
        from . import history
        return history.run(dry_run=a.dry_run, yes=a.yes)

    if a.cmd == "hook":
        from . import hookentry
        return hookentry.run(a.event, a.agent)
    if a.cmd in ("install", "uninstall") and a.agent == "git-hook":
        from . import githook
        return githook.install(remove=a.cmd == "uninstall")
    if a.cmd == "git-check":
        from . import githook
        return githook.check(a.kind)
    if a.cmd in ("install", "uninstall"):
        from . import agents
        return agents.install(a.agent, remove=a.cmd == "uninstall")
    if a.cmd == "doctor":
        if a.retry_key:
            from . import crypto
            crypto.clear_denial()
        return _doctor()
    if a.cmd == "export-onnx":
        import pathlib
        from . import onnx_backend, onnx_export
        if a.if_no_gpu:
            try:
                import torch
                if torch.backends.mps.is_available() or torch.cuda.is_available():
                    print("GPU found: the model runs fast on it, no CPU model needed")
                    return 0
            except Exception:
                pass
            if onnx_backend.model_file().exists() and (onnx_backend.model_dir() / "tokenizer.json").exists():
                print(f"CPU model already built: {onnx_backend.model_file()}")
                return 0
        # the first-start build skips the opt-in int8 copy: fp32 is the default, and quantizing needs the most memory
        onnx_export.export(pathlib.Path(a.out) if a.out else onnx_backend.model_dir(), int8=not a.if_no_gpu)
        return 0
    if a.cmd == "serve":
        from . import daemon
        daemon.serve()
        return 0
    if a.cmd == "dashboard":
        from . import client
        try:
            code = client._post("/dashboard/code", {})["code"]
        except client.Unavailable as e:
            print(f"cloakroom: {e}", file=sys.stderr)
            return 2
        url = f"http://127.0.0.1:{config.load()['port']}/dashboard?code={code}"
        if a.print:
            print(f"{url}\n(works once, for 60 seconds)")
        else:
            import webbrowser
            webbrowser.open(url)
            print("Opened the Cloakroom dashboard in your browser.")
        return 0
    if a.cmd == "status":
        cfg = config.load()
        from . import client
        health = client.health(cfg["port"]) or {"ok": False}
        if health.get("ok") and health.get("service") != "cloakroom":
            print(client.port_taken(cfg["port"]), file=sys.stderr)
            health = {"ok": False}
        sessions = len(list((config.HOME / "sessions").glob("*.json"))) if (config.HOME / "sessions").exists() else 0
        from . import crypto
        if crypto.denied():
            print(f"vault key: refused, your own rules are off. Retry: {crypto.RETRY}")
        print(f"mode: {cfg['mode']}  service: {'up' if health.get('ok') else 'down'}"
              f"{' (model loaded)' if health.get('model') else ''}  vault sessions: {sessions}")
        return 0

    mode = a.mode or config.load()["mode"]
    text = _read(a.file)
    try:
        cfg = config.load()
        r = engine.mask(text, mode=mode, allow=cfg["allow"], always=cfg["always"])
    except engine.ModelUnavailable as e:
        print(f"cloakroom: {e}. Use --mode rules to check with rules only.", file=sys.stderr)
        return 2
    if a.cmd == "mask":
        sys.stdout.write(r.text)
    elif a.cmd == "check":
        kinds = {}
        for _, kind, _ in r.masked:
            kinds[kind] = kinds.get(kind, 0) + 1
        print(f"[{mode}] would mask {len(r.masked)}: " + (", ".join(f"{k} x{n}" for k, n in sorted(kinds.items())) or "nothing")
              + f"; skipped {len(r.skipped)} as safe")
    else:
        for ph, kind, source in r.masked:
            print(f"MASK  {ph:<16} {kind:<8} by {source}")
        for kind, source, why in r.skipped:
            print(f"SKIP  {kind:<8} by {source}: {why}")
    return 1 if (a.cmd == "check" and r.masked) else 0


def _doctor() -> int:
    import platform
    ok = True

    def line(good, label, detail):
        nonlocal ok
        ok &= bool(good) or good is None
        print(f"{'ok ' if good else 'warn' if good is None else 'FAIL'}  {label}: {detail}")

    line(sys.version_info >= (3, 10), "python", f"{platform.python_version()} on {platform.system()} {platform.machine()}")
    cfg = config.load()
    line(True, "mode", cfg["mode"] + (" (rules only: names and addresses may be missed)" if cfg["mode"] == "rules" else ""))
    if cfg.get("installing"):
        line(None, "install", {"rules": "first-start install, stage 1 of 2 running",
                               "model": "first-start install, stage 2 of 2: the model is downloading",
                               "model-failed": "the model download failed; see ~/.cloakroom/bootstrap.log"}
             .get(cfg["installing"], cfg["installing"]))
    from . import crypto
    try:
        crypto.key()
        line(crypto.KEY_SOURCE != "key-file" or None, "vault key",
             {"os-secure-store": "OS secure store", "environment": "CLOAKROOM_VAULT_KEY",
              "key-file": "key file (no OS secure store found; set CLOAKROOM_VAULT_KEY or install a keyring backend)"}
             [crypto.KEY_SOURCE])
    except crypto.KeyUnavailable as e:
        line(False, "vault key", str(e))
        if crypto.denied():
            print(f"      {crypto.PREWARN}")
    except Exception as e:
        line(False, "vault key", f"{type(e).__name__}: install the 'cryptography' and 'keyring' packages")
    if cfg["mode"] == "full":
        import time
        try:
            backend = engine.choose_backend()
            print("...   model: loading (the first run downloads it once, about 1 GB; that can take a minute or two)",
                  flush=True)
            t0 = time.perf_counter()
            engine._model()
            t1 = time.perf_counter()
            sample = "Contact Jane Example at jane@example.org or +1 415 555 0100."
            engine.mask(sample, mode="full")  # the first check warms the backend up
            t2 = time.perf_counter()
            engine.mask(sample, mode="full")
            t3 = time.perf_counter()
            detail = backend + (f" ({engine._model().path.name})" if backend == "onnx" else "")
            line(True, "model", f"{detail}, loaded in {t1 - t0:.1f} s, one check {1000 * (t3 - t2):.0f} ms "
                                f"(first check {1000 * (t2 - t1):.0f} ms, a one-time warm-up)")
        except engine.ModelUnavailable as e:
            line(False, "model", f"{e} (or set mode to rules)")
    from . import client
    h = client.health(cfg["port"])
    if h is None:
        line(True, "service", f"not running yet (port {cfg['port']}); it starts by itself on first use")
    elif h.get("service") != "cloakroom":
        line(False, "service", client.port_taken(cfg["port"]))
    else:
        line(True, "service", f"up on port {cfg['port']}" + (", model loaded" if h.get("model") else ""))
    import os
    from . import agents
    if os.path.isdir(agents.codex_home()):  # Codex is on this machine
        on = agents.codex_installed()
        line(on or None, "codex", "Cloakroom's hooks installed; Codex runs them only once you trust them (/hooks in "
             "Codex)" if on else "not protected; to protect it: cloakroom install codex")
    if os.path.isdir(agents.gemini_home()):  # Gemini CLI is on this machine
        on = agents.gemini_installed()
        line(on or None, "gemini", f"Cloakroom's extension installed ({agents.gemini_dir()})" if on else
             "not protected; to protect it: cloakroom install gemini")
        for problem in agents.gemini_problems(os.getcwd()) if on else []:
            line(None, "gemini", problem)
    found = find_program()
    line(found is not None or None, "program", found or "the plugin can't find the cloakroom program, so Claude Code "
         "tool calls are denied: reinstall (uv tool install 'cloakroom[model,torch]', or let the plugin do it by "
         "deleting ~/.cloakroom/bootstrap.state and restarting Claude Code)")
    return 0 if ok else 1


def find_program() -> str | None:
    """Where the plugin's hooks find the program: PATH, uv's tool folder, ~/.local/bin (pipx) or ~/.cloakroom/venv
    (plugin/hooks: the same order). The last three are often missing from the PATH Claude Code sees, which is fine."""
    import os
    import shutil
    import subprocess
    exe = "cloakroom.exe" if os.name == "nt" else "cloakroom"
    cands = [shutil.which("cloakroom")]
    try:
        out = subprocess.run(["uv", "tool", "dir", "--bin"], capture_output=True, text=True, timeout=10)
        cands.append(os.path.join(out.stdout.strip(), exe) if out.returncode == 0 else None)
    except Exception:
        pass
    venv = os.path.join(str(config.HOME), "venv", "Scripts" if os.name == "nt" else "bin", exe)
    cands += [os.path.join(os.path.expanduser("~"), ".local", "bin", exe), venv]
    return next((c for c in cands if c and os.path.isfile(c)), None)


if __name__ == "__main__":
    sys.exit(main())
