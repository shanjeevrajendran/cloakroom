"""Session map between real values and placeholders. Stays on this machine; never logged.

Session files are AES-GCM encrypted (see crypto.py); an old plain-JSON file is read once and re-saved encrypted.
Placeholders are shell- and regex-safe tokens (PII_EMAIL_1_k7q2ab): letters, digits and underscores only.
The same value always gets the same placeholder within a session, so masked output stays consistent.

Signed placeholders (0.6.1): the last part is a tag only this machine can make, an HMAC over the vault's random id,
the session and the placeholder's name, keyed from the vault key (never stored). Restore gives a value back only
for a placeholder this session issued: a guessed number, a wrong tag, one from another session or one from before
0.6.1 (PII_EMAIL_1) stays as text and is reported as refused.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path

TAG_LEN = 6  # base32 characters: 30 bits
PLACEHOLDER = re.compile(r"\bPII_[A-Z]+_\d+_[a-z2-7]{6}\b")  # what Cloakroom issues: the only form restored
ANY_PLACEHOLDER = re.compile(r"\bPII_[A-Z]+_\d+(?:_[a-z2-7]{6})?\b")  # also the form from before 0.6.1
_PARTS = re.compile(r"(PII_[A-Z]+_\d+)(?:_([a-z2-7]{6}))?")
TAG_INFO = b"cloakroom-placeholder-tag-v1"

OLD_FORMAT, NOT_ISSUED = "old-format", "not-issued"


def kind_of(placeholder: str) -> str:
    return placeholder.split("_")[1]


class Vault:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else None
        self.session = self.path.stem if self.path else ""
        self.by_value: dict[str, str] = {}
        self.by_placeholder: dict[str, str] = {}
        self.counts: dict[str, int] = {}
        self.derived: set[str] = set()  # placeholders of copies derived from a known value: never seeds themselves
        self.refused: list[tuple[str, str]] = []  # (placeholder, reason) refused by this instance
        self.id = secrets.token_hex(16)
        self._key = None if self.path else secrets.token_bytes(32)  # no file: tags only need to hold for this object
        if self.path and self.path.exists():
            from . import crypto
            raw = self.path.read_bytes()
            data = json.loads(raw) if raw.startswith(b"{") else json.loads(crypto.decrypt(raw))
            # a vault from before 0.6.1 has no id: derive one from the file, so every process reading it agrees
            self.id = data.get("id") or hashlib.sha256(raw).hexdigest()[:32]
            self.counts = data["counts"]
            # and its values get signed placeholders with the same numbers; the old names are no longer restored
            self.by_placeholder = {(ph if PLACEHOLDER.fullmatch(ph) else self._sign(ph)): v
                                   for ph, v in data["map"].items()}
            self.by_value = {v: k for k, v in self.by_placeholder.items()}
            self.derived = set(data.get("derived", []))  # 0.6.6+; derived copies are always signed

    def _tag(self, name: str) -> str:
        if self._key is None:
            from . import crypto
            self._key = hmac.new(crypto.key(), TAG_INFO, hashlib.sha256).digest()
        mac = hmac.new(self._key, f"{self.id}|{self.session}|{name}".encode(), hashlib.sha256).digest()
        return base64.b32encode(mac).decode().lower()[:TAG_LEN]

    def _sign(self, name: str) -> str:
        return f"{name}_{self._tag(name)}"

    def placeholder(self, value: str, kind: str, derived: bool = False) -> str:
        """derived: value is a hash, encoding or part of a known value (decode.find_known), kept out of the seeds."""
        if value in self.by_value:
            return self.by_value[value]
        self.counts[kind] = self.counts.get(kind, 0) + 1
        ph = self._sign(f"PII_{kind}_{self.counts[kind]}")
        self.by_value[value], self.by_placeholder[ph] = ph, value
        if derived:
            self.derived.add(ph)
        self._save()
        return ph

    def lookup(self, placeholder: str) -> str | None:
        """The real value for a placeholder this session issued, else None (and the refusal is noted)."""
        m = _PARTS.fullmatch(placeholder)
        if not m or not m.group(2):
            self.refused.append((placeholder, OLD_FORMAT))
            return None
        value = self.by_placeholder.get(placeholder)
        if value is None or not hmac.compare_digest(m.group(2), self._tag(m.group(1))):
            self.refused.append((placeholder, NOT_ISSUED))
            return None
        return value

    def restore(self, text: str) -> tuple[str, list[str]]:
        """Swap issued placeholders back to real values. Returns the text and the placeholders left as text."""
        unknown = []

        def sub(m):
            value = self.lookup(m.group())
            if value is None:
                unknown.append(m.group())
                return m.group()
            return value
        return ANY_PLACEHOLDER.sub(sub, text), unknown

    def _save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        from . import crypto
        blob = crypto.encrypt(json.dumps({"id": self.id, "map": self.by_placeholder, "counts": self.counts,
                                       "derived": sorted(self.derived)}).encode())
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        os.replace(tmp, self.path)
