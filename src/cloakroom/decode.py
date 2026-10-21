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


# Known secrets in encoded form. Rather than decode every base64-looking run (false alarms), each known value is
# encoded the ways a command or a file would carry it, and those exact strings are looked for. A hit is derived
# from one stored secret, so ordinary text can't trip it.
KNOWN_MIN = 8  # shorter values are too likely to turn up by chance
_B64_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/_-")


def _b64_cores(value: str) -> set[str]:
    """The part of value's base64 that doesn't depend on what comes before or after it, at each of the three byte
    offsets, standard and URL-safe: so 'user:<secret>' encoded as a whole (Basic auth) still holds a match."""
    raw, out = value.encode(), set()
    for shift in range(3):
        enc = base64.b64encode(b"\0" * shift + raw).decode().rstrip("=")
        core = enc[-(-8 * shift // 6):(8 * (shift + len(raw))) // 6]
        if len(core) >= KNOWN_MIN:
            out |= {core, core.replace("+", "-").replace("/", "_")}
    return out


def encoded_forms(value: str) -> dict[str, str]:
    """{encoded string: how} for one known value: base64 (both alphabets, any padding), URL-encoded, JSON-escaped.
    Forms equal to the value itself are left out (the plain value is matched on its own)."""
    import json
    from urllib.parse import quote, quote_plus
    forms = {f: "base64" for f in _b64_cores(value)}
    for f in (quote(value, safe=""), quote(value), quote_plus(value)):
        forms.setdefault(f, "URL-encoded")
        forms.setdefault(re.sub(r"%[0-9A-F]{2}", lambda m: m.group().lower(), f), "URL-encoded")
    for f in (json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1]):
        forms.setdefault(f, "JSON-escaped")
        forms.setdefault(f.replace("/", "\\/"), "JSON-escaped")
    return {f: how for f, how in forms.items() if f != value and len(f) >= KNOWN_MIN}


_KNOWN_CACHE: dict = {}


def _known_regex(values: tuple[str, ...]):
    if values not in _KNOWN_CACHE:
        table = {}
        for v in values:
            if len(v) < KNOWN_MIN:
                continue
            table.setdefault(v, (v, "plain"))
            for f, how in encoded_forms(v).items():
                table.setdefault(f, (v, how))
        rx = re.compile("|".join(re.escape(f) for f in sorted(table, key=len, reverse=True))) if table else None
        if len(_KNOWN_CACHE) > 64:
            _KNOWN_CACHE.clear()
        _KNOWN_CACHE[values] = (rx, table)
    return _KNOWN_CACHE[values]


def find_known(text: str, values) -> list[tuple[int, int, str, str]]:
    """(start, end, known value, how) for each place a known value appears, plain or encoded. A base64 hit is
    widened to the whole base64 run around it, so no piece of the encoding is left showing."""
    rx, table = _known_regex(tuple(sorted(set(values))))
    if rx is None:
        return []
    out = []
    for m in rx.finditer(text):
        value, how = table[m.group()]
        a, b = m.span()
        if how == "base64":
            while a > 0 and text[a - 1] in _B64_CHARS:
                a -= 1
            while b < len(text) and text[b] in _B64_CHARS:
                b += 1
            while b < len(text) and text[b] == "=":
                b += 1
        out.append((a, b, value, how))
    return out
