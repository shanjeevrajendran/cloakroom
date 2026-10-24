"""Encoded-output check. A command can print a file in a form detectors can't read: `od -c` spaces out every
character, `xxd`/`hexdump` print hex, `base64` and `rev` scramble it. This builds decoded views of an output so
the engine (and the session's known values) can be checked against them. Any hit withholds the whole output.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
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


_HASHES = ("md5", "sha1", "sha256")
REVERSED_MIN = 12  # a reversed short value turns up by chance; keys and passwords this long don't
HASH_ONLY = {"EMAIL"}  # matched only as hashes here: the plain value is the email rule's job
REVERSED_KINDS = {"SECRET", "PASSWORD"}


def derived_forms(value: str, kind: str = "SECRET") -> dict[str, str]:
    """{form: how} for one known value: the encodings above, MD5/SHA-1/SHA-256 hex, hex of its bytes and, for keys
    and passwords of 12+ characters, the value reversed. An email gets hashes only, of the value and of it trimmed
    and lowercased (how sites hash an email). Hash and hex forms are lowercase and contiguous: find_known reads hex
    in any case and in NN NN, NN:NN and \\xNN styles. Forms equal to the value are left out."""
    seeds = {value, value.strip().lower()} if kind in HASH_ONLY else {value}
    forms = {hashlib.new(name, s.encode()).hexdigest(): "hash" for s in seeds for name in _HASHES}
    if kind not in HASH_ONLY:
        forms.update(encoded_forms(value))
        forms.setdefault(value.encode().hex(), "hex")
        if kind in REVERSED_KINDS and len(value) >= REVERSED_MIN and value[::-1] != value:
            forms.setdefault(value[::-1], "reversed")
    return {f: how for f, how in forms.items() if f != value and len(f) >= KNOWN_MIN}


_KNOWN_CACHE: dict = {}


def _as_kinds(values) -> dict[str, str]:
    return dict(values) if isinstance(values, dict) else {v: "SECRET" for v in values}


def _known_regex(values: tuple[tuple[str, str], ...]):
    """Lookup tables, built once per set of known values: a regex over plain and encoded forms with {form: (value,
    how)}, {hash: value}, {first 16 hex digits: [(hex, value)]} and [(reversed, value)]. Hashes, hex and reversed
    values stay out of the regex: a long alternation crawls on hex-heavy output (lockfiles, git logs) and on forms
    that start with random characters."""
    if values not in _KNOWN_CACHE:
        table, hashes, hexes, backwards = {}, {}, {}, []
        for v, kind in values:
            if len(v) < KNOWN_MIN:
                continue
            if kind not in HASH_ONLY:
                table.setdefault(v, (v, "plain"))
            for f, how in derived_forms(v, kind).items():
                if how == "hash":
                    hashes.setdefault(f, v)
                elif how == "hex":
                    hexes.setdefault(f[:_HEX_KEY], []).append((f, v))
                elif how == "reversed":
                    backwards.append((f, v))
                else:
                    table.setdefault(f, (v, how))
        rx = re.compile("|".join(re.escape(f) for f in sorted(table, key=len, reverse=True))) if table else None
        if len(_KNOWN_CACHE) > 64:
            _KNOWN_CACHE.clear()
        _KNOWN_CACHE[values] = (rx, table, hashes, hexes, backwards)
    return _KNOWN_CACHE[values]


_H = "[0-9a-fA-F]"
_HEX_KEY = 2 * KNOWN_MIN  # hex of a known value is at least this long
# a hex run: \xNN escapes, NN NN / NN:NN pairs, or contiguous digits (a 0x prefix is taken in by _word_edges)
_HEX_RUN = re.compile(rf"(?<!{_H})(?:(?:\\x{_H}{{2}}){{4,}}|{_H}{{2}}(?:[ :]{_H}{{2}}){{3,}}|{_H}{{8,}})(?!{_H})")


def _hex_hits(text: str, hashes: dict, hexes: dict) -> list[tuple[int, int, str, str]]:
    """A whole hex run that is a known value's hash, or that holds a known value's hex at a byte boundary."""
    out = []
    for m in _HEX_RUN.finditer(text):
        digits = m.group().replace("\\x", "").replace(" ", "").replace(":", "").lower()
        if digits in hashes:
            out.append((m.start(), m.end(), hashes[digits], "hash"))
            continue
        hit = next((v for i in range(0, len(digits) - _HEX_KEY + 1, 2)  # byte boundaries only
                    for h, v in hexes.get(digits[i:i + _HEX_KEY], ()) if digits.startswith(h, i)), None)
        if hit:
            out.append((m.start(), m.end(), hit, "hex"))
    return out


def _word_edges(text: str, a: int, b: int) -> tuple[int, int]:
    """Grow a derived hit to the word around it ("0x<hex>", "%3D<value>", "x2093"): a placeholder glued to a letter
    or digit has no word boundary, so restore wouldn't see it."""
    while a > 0 and (text[a - 1].isalnum() or text[a - 1] == "_"):
        a -= 1
    while b < len(text) and (text[b].isalnum() or text[b] == "_"):
        b += 1
    return a, b


def find_known(text: str, values) -> list[tuple[int, int, str, str]]:
    """(start, end, known value, how) for each place a known value appears, plain or encoded. values: {value: kind}
    (or a plain list, taken as keys). A base64 or hex hit covers the whole run around it, so no piece of the encoding
    is left showing; a hash counts only as a whole hex run, not as part of a longer one."""
    rx, table, hashes, hexes, backwards = _known_regex(tuple(sorted(_as_kinds(values).items())))
    out = []
    for m in rx.finditer(text) if rx else ():
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
    if hashes or hexes:
        out += _hex_hits(text, hashes, hexes)
    for f, v in backwards:
        i = text.find(f)
        while i >= 0:
            out.append((i, i + len(f), v, "reversed"))
            i = text.find(f, i + len(f))
    return [(a, b, v, how) if how == "plain" else (*_word_edges(text, a, b), v, how) for a, b, v, how in out]


# Parts of a known value, only where the text says so. A card's last 4 digits turn up in prices and order numbers,
# so they count only right after a cue such as "ending in" or "****". An email's local part counts on its own only
# when it can't be an ordinary word: 6+ characters, not a role name, and with a digit or . _ - in it unless 8+ long.
PART_KINDS = {"CARD", "ACCOUNT", "BANK", "IBAN"}
_LAST4_CUE = re.compile(r"(?:\bending(?:\s+in)?|\blast\s*(?:4|four)(?:\s+digits)?|[*xX\u2022\u2026]{2,}|\.{3,}|\b[xX])"
                        r"[^\d\n]{0,12}$", re.I)
ROLE_WORDS = {"accounting", "accounts", "billing", "careers", "contact", "customer", "customers", "feedback",
              "hostmaster", "info", "invoice", "invoices", "marketing", "newsletter", "noreply", "no-reply",
              "do-not-reply", "donotreply", "notifications", "notify", "office", "orders", "postmaster", "privacy",
              "receipts", "recruiting", "reply", "sales", "security", "service", "support", "webmaster", "welcome",
              "admin", "administrator", "hello", "help", "team", "abuse", "mailer-daemon", "payments", "press"}


def local_part(email: str) -> str | None:
    """An email's local part when it is distinctive enough to stand for the person on its own, else None."""
    lp = email.split("@")[0].strip()
    if len(lp) < 6 or lp.lower() in ROLE_WORDS or not re.fullmatch(r"[\w.+-]+", lp):
        return None
    if not re.search(r"[\d._-]", lp) and len(lp) < 8:
        return None
    return lp


def find_parts(text: str, values, own=lambda s: False) -> list[tuple[int, int, str, str]]:
    """(start, end, known value, "part"): a known card or account number's last 4 after a cue, and a known
    email's distinctive local part as a whole word (case-insensitive). own(part): True for this machine's own
    account name, which is never masked this way."""
    out, last4 = [], {}
    for v, kind in _as_kinds(values).items():
        if kind in PART_KINDS:
            digits = re.sub(r"\D", "", v)
            if len(digits) >= 8:
                last4.setdefault(digits[-4:], v)
        elif kind == "EMAIL" and (lp := local_part(v)) and not own(lp):
            rx = re.compile(r"(?<![\w.%+-])" + re.escape(lp) + r"(?![\w%+-]|@|\.\w)", re.I)
            out += [(m.start(), m.end(), v, "part") for m in rx.finditer(text)]
    for d, v in last4.items():  # each place the 4 digits stand alone, then a cue just before them
        for m in re.finditer(rf"(?<!\d){d}(?!\d)", text):
            if _LAST4_CUE.search(text, max(0, m.start() - 30), m.start()):
                out.append((*_word_edges(text, *m.span()), v, "part"))
    return out
