"""Encoded-output check. A command can print a file in a form detectors can't read: `od -c` spaces out every
character, `xxd`/`hexdump` print hex, `base64` and `rev` scramble it. This builds decoded views of an output so
the engine (and the session's known values) can be checked against them. Any hit withholds the whole output.
"""
from __future__ import annotations

import base64
import binascii
import re

_OFFSET = re.compile(r"^\s*[0-9a-fA-F]{6,8}[:\s]")
_HEX_PAIRS = re.compile(r"(?:\b[0-9a-fA-F]{2}\b[ ]?){4,}|(?:\b[0-9a-fA-F]{4}\b[ ]?){4,}")
_B64 = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
_OD_ESCAPES = {"\\n": "\n", "\\t": "\t", "\\r": "", "\\0": ""}


def _od_chars(text: str) -> str:
    """od -c / od -a style: an offset, then one character per cell."""
    out = []
    for line in text.splitlines():
        if not _OFFSET.match(line):
            continue
        cells = line.split()[1:]
        out.append("".join(_OD_ESCAPES.get(c, c) if len(c) <= 3 else "" for c in cells))
    return "".join(out)


def _hex_dump(text: str) -> str:
    out = []
    for line in text.splitlines():
        body = _OFFSET.sub("", line, count=1)
        for m in _HEX_PAIRS.finditer(body):
            digits = re.sub(r"\s", "", m.group())
            try:
                out.append(binascii.unhexlify(digits[: len(digits) // 2 * 2]).decode("utf-8", "ignore"))
            except (binascii.Error, ValueError):
                pass
    return "".join(out)


def _base64_runs(text: str) -> str:
    out = []
    for m in _B64.finditer(text):
        s = m.group()
        try:
            raw = base64.b64decode(s + "=" * (-len(s) % 4), altchars=b"-_" if ("-" in s or "_" in s) else None)
        except (binascii.Error, ValueError):
            continue
        # real encoded text decodes to plain ASCII; random path or hash segments decode to binary or odd Unicode
        if raw and sum(32 <= b < 127 or b in (9, 10, 13) for b in raw) / len(raw) > 0.95:
            out.append(raw.decode("ascii", "ignore"))
    return "\n".join(out)


def views(text: str, reverse: bool = False) -> list[str]:
    """Decoded readings of text worth re-scanning (empty strings dropped). reverse: also read lines backwards."""
    candidates = [_od_chars(text), _hex_dump(text), _base64_runs(text)]
    if reverse:
        candidates.append("\n".join(line[::-1] for line in text.splitlines()))
    return [v for v in candidates if len(v.strip()) >= 6]


def squeeze(s: str) -> str:
    return re.sub(r"\s+", "", s)


FRAGMENT = 10  # a run this long from a known value counts, so truncated output (head, cut) is caught too


def known_value_hit(text: str, values) -> bool:
    """Whitespace-insensitive check for any of the session's real values, or a 10+ character run of one."""
    flat = squeeze(text)
    for v in values:
        v = squeeze(v)
        if len(v) < 6:
            continue
        if len(v) <= FRAGMENT:
            if v in flat:
                return True
            continue
        if any(v[i:i + FRAGMENT] in flat for i in range(len(v) - FRAGMENT + 1)):
            return True
    return False
