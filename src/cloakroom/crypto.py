"""Vault encryption at rest, the same on macOS, Windows and Linux.

One random 256-bit key per user, kept in the OS secure store through `keyring` (macOS Keychain, Windows Credential
Manager/DPAPI, Linux Secret Service). Vault files are AES-GCM encrypted with it. Key lookup order:
  1. CLOAKROOM_VAULT_KEY (base64), for CI and headless machines
  2. the OS secure store
  3. a key file readable only by you, when no secure store exists (headless Linux, some WSL); `cloakroom doctor`
     says so. Vaults are never written in plain text.
A secure store that exists but refuses access (a locked macOS Keychain in an SSH shell) raises KeyUnavailable: a
new key file then would orphan everything sealed with the stored key.
"""
from __future__ import annotations

import base64
import os
import secrets

from . import config

SERVICE, ACCOUNT = "cloakroom", "vault-key"
MAGIC, AAD = b"CRV1", b"cloakroom-vault-v1"
_KEY: bytes | None = None
KEY_SOURCE = ""


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


def key() -> bytes:
    global _KEY, KEY_SOURCE
    if _KEY:
        return _KEY
    env = os.environ.get("CLOAKROOM_VAULT_KEY")
    if env:
        _KEY, KEY_SOURCE = base64.b64decode(env), "environment"
        return _KEY
    store = _secure_store()
    if store:
        try:
            stored = store.get_password(SERVICE, ACCOUNT)
            if not stored:
                stored = base64.b64encode(secrets.token_bytes(32)).decode()
                store.set_password(SERVICE, ACCOUNT, stored)
            _KEY, KEY_SOURCE = base64.b64decode(stored), "os-secure-store"
            return _KEY
        except Exception as e:
            raise KeyUnavailable(f"can't read the vault key from the OS secure store ({type(e).__name__}); "
                                 "unlock it (macOS over SSH: security unlock-keychain) or set CLOAKROOM_VAULT_KEY") from e
    path = config.private_dir() / "vault.key"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(base64.b64encode(secrets.token_bytes(32)).decode())
    _KEY, KEY_SOURCE = base64.b64decode(path.read_text().strip()), "key-file"
    return _KEY


def encrypt(data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    return MAGIC + nonce + AESGCM(key()).encrypt(nonce, data, AAD)


def decrypt(blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob.startswith(MAGIC):
        raise ValueError("not a Cloakroom vault")
    return AESGCM(key()).decrypt(blob[4:16], blob[16:], AAD)
