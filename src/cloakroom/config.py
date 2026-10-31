"""Settings: ~/.cloakroom/config.json, overridden by CLOAKROOM_* environment variables."""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

HOME = Path(os.environ.get("CLOAKROOM_HOME", Path.home() / ".cloakroom"))
DEFAULTS = {"mode": "full", "port": 37787,  # not 8787: Headroom's proxy defaults to it
            "allow": [], "mask_own_username": False, "mask_all_dates": False,
            "allow_images": False, "ocr": True, "ocr_max_pages": 20, "ocr_timeout": 30, "log_months": 12,
            "codex_hold_long_running": False,  # Codex: hold servers and watchers (hook.py _long_hold)
            "egress": "off",  # "allowlist": a call to a site not in allowed_hosts is held (egress.py, hook.py _site_hold)
            "allowed_hosts": [],  # hosts you approve, or *.example.com; `cloakroom allow-host` edits it
            "scrub_transcript": True,
            "proxy_port": 0,  # `cloakroom proxy`: 0 = pick a free port once and keep it (proxy.json)
            "proxy_max_mb": 16,  # a request body bigger than this is refused, not sent (logged as denied)
            "proxy_upstreams": {}}  # more prefixes for the proxy: {"/mistral": "https://api.mistral.ai"}  # take a stopped prompt out of the session's saved transcript (history.py)


def load() -> dict:
    cfg = dict(DEFAULTS)
    path = HOME / "config.json"
    if path.exists():
        cfg.update(json.loads(path.read_text()))
    if os.environ.get("CLOAKROOM_MODE"):
        cfg["mode"] = os.environ["CLOAKROOM_MODE"]
    if os.environ.get("CLOAKROOM_PORT"):
        cfg["port"] = int(os.environ["CLOAKROOM_PORT"])
    if cfg["mode"] not in ("full", "rules"):
        raise ValueError("mode must be 'full' or 'rules'")
    if cfg["egress"] not in ("off", "allowlist"):
        raise ValueError("egress must be 'off' or 'allowlist'")
    if not isinstance(cfg["proxy_upstreams"], dict):
        cfg["proxy_upstreams"] = {}
    cfg["allowed_hosts"] = [h for h in cfg["allowed_hosts"] if isinstance(h, str)] \
        if isinstance(cfg["allowed_hosts"], list) else []
    # While the plugin's first-start installer is still fetching the model (plugin/scripts/bootstrap.sh), run on
    # the rules so the user is protected from the first minute; full mode switches on when the state file goes.
    # Rules you approved on the dashboard (userrules.py, encrypted): add to the allowlist, and an always-mask list.
    cfg["always"] = []
    if (HOME / "rules.bin").exists():
        from . import userrules
        allow, always = userrules.effective()
        cfg["allow"], cfg["always"] = list(cfg["allow"]) + allow, always
    state = install_state()
    if cfg["mode"] == "full" and state in ("rules", "model", "model-failed"):
        cfg["mode"], cfg["installing"] = "rules", state
    return cfg


def set_allowed_hosts(hosts: list[str]):
    """Write allowed_hosts into config.json, leaving every other setting as it is."""
    path = private_dir() / "config.json"
    cfg = json.loads(path.read_text()) if path.exists() else {}
    cfg["allowed_hosts"] = hosts
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


def install_state() -> str | None:
    """The first-start installer's stage: rules | model | model-failed | failed, or None when it isn't running."""
    try:
        return (HOME / "bootstrap.state").read_text().strip() or None
    except OSError:
        return None


def private_dir(name: str = "") -> Path:
    d = HOME / name if name else HOME
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)
    return d


def token() -> str:
    """Shared secret between hooks and the local service, so no other local process can call /restore."""
    path = private_dir() / "token"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32))
    return path.read_text().strip()


def vault_path(session: str) -> Path:
    safe = "".join(c for c in session if c.isalnum() or c in "-_")[:80] or "default"
    return private_dir("sessions") / f"{safe}.json"
