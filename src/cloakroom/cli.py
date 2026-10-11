"""cloakroom CLI. Never prints real values except `restore`, which is for you, not the agent.

  cloakroom check [FILE|-] [--mode full|rules]   what would be masked, and what was skipped as safe (no values)
  cloakroom mask  [FILE|-] [--mode ...]          print the masked text
  cloakroom explain [FILE|-]                     every finding with its rule and reason
  cloakroom serve                                run the local service (keeps the model loaded)
  cloakroom status                               mode, service health, vault sessions
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from . import config, engine


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
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("check", "mask", "explain"):
        s = sub.add_parser(name)
        s.add_argument("file", nargs="?", default="-")
        s.add_argument("--mode", choices=["full", "rules"])
    h = sub.add_parser("hook", help="Claude Code hook entry point (reads hook JSON on stdin)")
    h.add_argument("event", choices=["start", "prompt", "pre", "post"])
    sub.add_parser("doctor", help="check this machine: Python, vault key store, model, service")
    x = sub.add_parser("export-onnx", help="export the model to ONNX for fast CPU inference (needs cloakroom[torch] once)")
    x.add_argument("--out", default=None, help="directory (default: the Cloakroom home)")
    sub.add_parser("serve")
    sub.add_parser("status")
    a = p.parse_args(argv)

    if a.cmd == "hook":
        from . import hookentry
        return hookentry.run(a.event)
    if a.cmd == "doctor":
        return _doctor()
    if a.cmd == "export-onnx":
        import pathlib
        from . import onnx_backend, onnx_export
        onnx_export.export(pathlib.Path(a.out) if a.out else onnx_backend.model_dir())
        return 0
    if a.cmd == "serve":
        from . import daemon
        daemon.serve()
        return 0
    if a.cmd == "status":
        cfg = config.load()
        from . import client
        health = client.health(cfg["port"]) or {"ok": False}
        if health.get("ok") and health.get("service") != "cloakroom":
            print(client.port_taken(cfg["port"]), file=sys.stderr)
            health = {"ok": False}
        sessions = len(list((config.HOME / "sessions").glob("*.json"))) if (config.HOME / "sessions").exists() else 0
        print(f"mode: {cfg['mode']}  service: {'up' if health.get('ok') else 'down'}"
              f"{' (model loaded)' if health.get('model') else ''}  vault sessions: {sessions}")
        return 0

    mode = a.mode or config.load()["mode"]
    text = _read(a.file)
    try:
        r = engine.mask(text, mode=mode, allow=config.load()["allow"])
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
    try:
        from . import crypto
        crypto.key()
        line(crypto.KEY_SOURCE != "key-file" or None, "vault key",
             {"os-secure-store": "OS secure store", "environment": "CLOAKROOM_VAULT_KEY",
              "key-file": "key file (no OS secure store found; set CLOAKROOM_VAULT_KEY or install a keyring backend)"}
             [crypto.KEY_SOURCE])
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
    import shutil
    line(bool(shutil.which("cloakroom")) or None, "on PATH", shutil.which("cloakroom") or
         "cloakroom isn't on PATH: Claude Code tool calls are denied until it is; Windows: uv tool update-shell, then a new terminal")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
