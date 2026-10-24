"""Vault encryption at rest, the same on macOS, Windows and Linux.

One random 256-bit key per user, kept in the OS secure store through `keyring` (macOS Keychain, Windows Credential
Manager/DPAPI, Linux Secret Service). Vault files are AES-GCM encrypted with it. Key lookup order:
  1. CLOAKROOM_VAULT_KEY (base64, or 64 hex characters), for CI and headless machines
  2. the OS secure store
  3. a key file readable only by you, when no secure store exists (headless Linux, some WSL); `cloakroom doctor`
     says so. Vaults are never written in plain text.
A secure store that exists but refuses access (a locked macOS Keychain in an SSH shell) raises KeyUnavailable: a
new key file then would orphan everything sealed with the stored key. The refusal is remembered in a marker file
(~/.cloakroom/key-denied, no secret in it) so no process asks again, and the macOS prompt doesn't repeat, until
`cloakroom serve` starts or `cloakroom doctor --retry-key` runs.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import sys
import time

from . import config

SERVICE, ACCOUNT = "cloakroom", "vault-key"
MAGIC, AAD = b"CRV1", b"cloakroom-vault-v1"
_KEY: bytes | None = None
KEY_SOURCE = ""


STORE = "macOS Keychain" if sys.platform == "darwin" else "system key store"
PREWARN = ("macOS may ask whether 'python' can use the 'cloakroom' item in your Keychain. Click Always Allow: it "
           "only lets Cloakroom read its own key. If you deny, your own rules turn off; built-in checks keep running.")
RETRY = "cloakroom doctor --retry-key"
DENIED = (f"Cloakroom can't read its key from the {STORE} (access was denied, or the {STORE} is locked, for example "
          "over SSH), so your own rules are off. Built-in checks still run. To turn them back on: unlock it or click "
          f"Always Allow, then run: {RETRY}")


class KeyUnavailable(RuntimeError):
    """The OS secure store holds the key but won't hand it over right now."""


def _secure_store():
    try:
        import keyring
        from keyring.backends import fail
        kr = keyring.get_keyring()
        if isinstance(kr, fail.Keyring) or type(kr).__name__ in ("NullKeyring", "PlaintextKeyring"):
            return None
        return keyring
    except Exception:
        return None


def _marker():
    return config.HOME / "key-denied"


def denied() -> bool:
    return _marker().exists()


def clear_denial():
    _marker().unlink(missing_ok=True)


def denial_notice() -> str | None:
    """DENIED once per denial (then the marker says notified), else None."""
    try:
        state = json.loads(_marker().read_text())
        if state.get("notified"):
            return None
        _write_marker(state | {"notified": True})
    except (OSError, ValueError):
        return None
    return DENIED


def _write_marker(state: dict):
    config.private_dir()
    fd = os.open(_marker(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(state, f)


def key() -> bytes:
    global _KEY, KEY_SOURCE
    if _KEY:
        return _KEY
    env = os.environ.get("CLOAKROOM_VAULT_KEY")
    if env:
        _KEY, KEY_SOURCE = _env_key(env.strip()), "environment"
        return _KEY
    store = _secure_store()
    if store:
        if denied():  # asked and refused: don't ask again (and don't prompt again) until cleared
            raise KeyUnavailable(DENIED)
        try:
            stored = store.get_password(SERVICE, ACCOUNT)
            if not stored:
                stored = base64.b64encode(secrets.token_bytes(32)).decode()
                store.set_password(SERVICE, ACCOUNT, stored)
            _KEY, KEY_SOURCE = base64.b64decode(stored), "os-secure-store"
            return _KEY
        except Exception as e:
            _write_marker({"at": time.time(), "notified": False})
            raise KeyUnavailable(f"can't read the vault key from the OS secure store ({type(e).__name__}); "
                                 "unlock it (macOS over SSH: security unlock-keychain) or set CLOAKROOM_VAULT_KEY") from e
    path = config.private_dir() / "vault.key"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(base64.b64encode(secrets.token_bytes(32)).decode())
    _KEY, KEY_SOURCE = base64.b64decode(path.read_text().strip()), "key-file"
    return _KEY


def _env_key(value: str) -> bytes:
    """CLOAKROOM_VAULT_KEY as a 256-bit key: base64, or 64 hex characters (`openssl rand -hex 32`). A hex string
    read as base64 gives 48 bytes, which AES-GCM refuses at the first vault write; say so up front instead."""
    if re.fullmatch(r"[0-9a-fA-F]{64}", value):
        return bytes.fromhex(value)
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError:
        raw = b""
    if len(raw) not in (16, 24, 32):
        raise KeyUnavailable("CLOAKROOM_VAULT_KEY must be a 256-bit key in base64 (openssl rand -base64 32) "
                             "or 64 hex characters (openssl rand -hex 32)")
    return raw


def encrypt(data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    return MAGIC + nonce + AESGCM(key()).encrypt(nonce, data, AAD)


def decrypt(blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob.startswith(MAGIC):
        raise ValueError("not a Cloakroom vault")
    return AESGCM(key()).decrypt(blob[4:16], blob[16:], AAD)
