"""Session map between real values and placeholders. Stays on this machine; never logged.

Session files are AES-GCM encrypted (see crypto.py); an old plain-JSON file is read once and re-saved encrypted.
Placeholders are shell- and regex-safe tokens (PII_EMAIL_1): letters, digits and underscores only.
The same value always gets the same placeholder within a session, so masked output stays consistent.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

PLACEHOLDER = re.compile(r"\bPII_[A-Z]+_\d+\b")


class Vault:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else None
        self.by_value: dict[str, str] = {}
        self.by_placeholder: dict[str, str] = {}
        self.counts: dict[str, int] = {}
        if self.path and self.path.exists():
            from . import crypto
            raw = self.path.read_bytes()
            data = json.loads(raw) if raw.startswith(b"{") else json.loads(crypto.decrypt(raw))
            self.by_placeholder = data["map"]
            self.counts = data["counts"]
            self.by_value = {v: k for k, v in self.by_placeholder.items()}

    def placeholder(self, value: str, kind: str) -> str:
        if value in self.by_value:
            return self.by_value[value]
        self.counts[kind] = self.counts.get(kind, 0) + 1
        ph = f"PII_{kind}_{self.counts[kind]}"
        self.by_value[value], self.by_placeholder[ph] = ph, value
        self._save()
        return ph

    def restore(self, text: str) -> tuple[str, list[str]]:
        """Swap known placeholders back to real values. Returns the text and any unknown placeholders."""
        unknown = []

        def sub(m):
            if m.group() in self.by_placeholder:
                return self.by_placeholder[m.group()]
            unknown.append(m.group())
            return m.group()
        return PLACEHOLDER.sub(sub, text), unknown

    def _save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        from . import crypto
        blob = crypto.encrypt(json.dumps({"map": self.by_placeholder, "counts": self.counts}).encode())
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        os.replace(tmp, self.path)
