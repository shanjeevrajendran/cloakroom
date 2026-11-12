"""Detect, filter and mask. Two modes:

- full  (default): rules + PII-Tracer model. Fails closed: if the model can't load, check() raises ModelUnavailable.
- rules: rules only, labelled as such in every result.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from bisect import bisect_left, bisect_right
from collections import OrderedDict

# The model is public: no Hugging Face login needed, so hide the "unauthenticated requests" warning.
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")  # the "Loading weights" bar on every mask / check
from dataclasses import dataclass, field

from . import decode, rules, safe, winlibs
from .vault import Vault

winlibs.add_dll_dirs()  # Windows: the VC++ runtime DLLs from msvc-runtime, before torch or onnxruntime load

MODEL_ID = "perplexity-ai/PII-Tracer"
MODEL_REVISION = "d25c16f2e57e321f6d2527715c01df9112f956f5"  # remote code reviewed 2026-10-08
SPAN_THRESHOLD = 0.8   # tuned on the 200-item gate test: true spans >= 0.96, false alarms <= 0.75
# Raising it was tried 2026-10-10: true spans go down to 0.959 and false alarms sit at 0.95-0.97, so no bar helps.
KIND_THRESHOLD: dict = {}
# The model reads a window at a time. A window is at most CHUNK_CHARS characters and at most WINDOW_TOKENS tokens:
# both backends drop what is past the model's 4096 tokens without a word, and dense text (logs, CSV, hex, minified
# JSON) holds far more tokens per character than prose (6000 characters of digits is 6000 tokens). Half the model's
# limit also costs about a third less time per character, measured on ONNX.
CHUNK_CHARS = 6000
WINDOW_TOKENS = 2048
OVERLAP_CHARS = 200    # the next window starts at most this far back, so a value on a cut is read whole once
MODEL_KIND = {"private_person": "PERSON", "private_email": "EMAIL", "private_phone": "PHONE",
              "private_address": "ADDRESS", "private_url": "URL", "private_date": "DATE",
              "account_number": "ACCOUNT", "secret": "SECRET"}


_EDGE_PUNCT = ",;\"'`"  # model spans sometimes take the separator next to a value


class ModelUnavailable(RuntimeError):
    """full mode was asked for but the model can't run. Install cloakroom[model,torch] or use mode='rules'."""


class OutOfTime(ModelUnavailable):
    """The model check was stopped: it would have run past the time its caller has (time_limit). Callers fail
    closed on it like on any ModelUnavailable."""


SLOW = "this check would take longer than the agent waits for Cloakroom"
_STOP_AT = [0.0]  # time.monotonic() after which model_findings stops; 0: no limit


class time_limit:
    """with time_limit(seconds): a model check inside raises OutOfTime between two windows once the time is up, so
    work nobody is waiting for any more stops (the service runs one check at a time). None: no limit. Zero or
    less: the time is up already, and the first window is not read."""

    def __init__(self, seconds: float | None):
        self.seconds = seconds

    def __enter__(self):
        self.before = _STOP_AT[0]
        if self.seconds is None:
            _STOP_AT[0] = 0.0
        else:
            _STOP_AT[0] = max(1e-9, time.monotonic() + (self.seconds if self.seconds > 0 else -1.0))

    def __exit__(self, *exc):
        _STOP_AT[0] = self.before


_MODEL = None
BACKEND = None  # "torch" or "onnx" once the model is loaded


def choose_backend() -> str:
    """CLOAKROOM_BACKEND=auto|torch|onnx. auto: torch on MPS/CUDA, else onnx when onnxruntime and the exported
    model are present, else torch."""
    choice = os.environ.get("CLOAKROOM_BACKEND", "auto")
    if choice not in ("auto", "torch", "onnx"):
        raise ValueError("CLOAKROOM_BACKEND must be 'auto', 'torch' or 'onnx'")
    if choice != "auto":
        return choice
    try:
        import torch
        if torch.backends.mps.is_available() or torch.cuda.is_available():
            return "torch"
    except Exception:  # not installed, or installed but broken (Windows without the VC++ runtime: OSError)
        pass
    try:
        import onnxruntime  # noqa: F401
        from . import onnx_backend
        if onnx_backend.model_file().exists() and (onnx_backend.model_dir() / "tokenizer.json").exists():
            return "onnx"  # a build killed half-way (before 0.2.2) left the network without its tokenizer
    except Exception:
        pass
    return "torch"


def _model():
    global _MODEL, BACKEND
    if _MODEL is None:
        backend = choose_backend()
        _MODEL = _onnx_model() if backend == "onnx" else _torch_model()
        BACKEND = backend
    return _MODEL


def _onnx_model():
    try:
        from . import onnx_backend
        path = onnx_backend.model_file()
        if not path.exists():
            raise ModelUnavailable(f"ONNX model not found at {path}: run cloakroom export-onnx "
                                   "(needs cloakroom[torch] once), or set CLOAKROOM_BACKEND=torch")
        return onnx_backend.OnnxModel(path)
    except ImportError as e:
        raise ModelUnavailable("onnx backend needs: pip install 'cloakroom[model]'") from e
    except OSError as e:  # a DLL that onnxruntime needs is missing (Windows)
        raise ModelUnavailable(winlibs.vcpp_hint(e)) from e


def _torch_model():
    try:
        import torch
        from transformers import AutoModel
    except ImportError as e:
        raise ModelUnavailable("torch backend needs: pip install 'cloakroom[model,torch]'") from e
    except OSError as e:  # a DLL that torch needs is missing (Windows: the Visual C++ runtime)
        raise ModelUnavailable(winlibs.vcpp_hint(e)) from e
    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    return AutoModel.from_pretrained(MODEL_ID, revision=MODEL_REVISION, trust_remote_code=True).to(device).eval()


def _fit(model, text: str) -> int:
    """How many leading characters of text fit in one window's tokens: len(text) when all of it does."""
    fit = getattr(model, "fit", None)
    if fit is not None:  # onnx_backend.OnnxModel
        return fit(text, WINDOW_TOKENS)
    tokenizer = getattr(model, "tokenizer", None)  # the PyTorch model's own
    if tokenizer is None:
        return len(text)  # a stand-in model (tests)
    ends = [e for _, e in tokenizer(text, return_offsets_mapping=True, truncation=True,
                                    max_length=WINDOW_TOKENS + 1)["offset_mapping"]]
    return len(text) if len(ends) <= WINDOW_TOKENS else max(ends[:WINDOW_TOKENS])


_CUT = re.compile(r"[ \t,;|]")


def _windows(text: str, fit) -> list[tuple[int, int]]:
    """(start, end) of each stretch of text the model reads, in order. Together they cover every character. A window
    ends at a line break when its second half has one, else at a space or separator, and the next one starts at the
    last line (or word) of this one: a value is then whole in at least one window, unless it is longer than the
    overlap. fit(chunk) says how many leading characters of chunk the model reads."""
    out, pos, n = [], 0, len(text)
    while pos < n:
        end = min(n, pos + CHUNK_CHARS)
        end = pos + max(1, fit(text[pos:end]))
        half = pos + (end - pos) // 2
        if end < n:
            cut = text.rfind("\n", half, end)
            if cut < 0:
                cut = max(text.rfind(c, half, end) for c in " \t,;|")
            if cut >= 0:
                end = cut + 1
        out.append((pos, end))
        if end >= n:
            break
        low = max(half, end - OVERLAP_CHARS)
        line = text.rfind("\n", low, end - 1)
        word = _CUT.search(text, low, end - 1)
        pos = max(pos + 1, line + 1 if line >= 0 else word.end() if word else low)
    return out


KEEP_WINDOWS = 512
_READ: OrderedDict = OrderedDict()  # hash of a window's text -> what the model found in it, most recent last
_READ_BY = [None]  # the model those came from


def _predict(model, chunk: str) -> list[tuple[int, int, str, float]]:
    """(start, end, label, score) of the model's spans in chunk. The same window is often read twice in a row: a file
    a command names is checked before the command runs and again as its output. The answer is kept under a hash of
    the text (never the text), for the model that gave it."""
    if _READ_BY[0] is not model:
        _READ.clear()
        _READ_BY[0] = model
    key = hashlib.sha256(chunk.encode("utf-8", "surrogatepass")).digest()
    spans = _READ.get(key)
    if spans is None:
        spans = [(s.start, s.end, s.label, s.score) for s in model.predict(chunk)[0]]
        _READ[key] = spans
        while len(_READ) > KEEP_WINDOWS:
            _READ.popitem(last=False)
    else:
        _READ.move_to_end(key)
    return spans


def model_findings(text: str) -> list[rules.Finding]:
    model = _model()
    out: dict = {}  # in order, without the doubles from the overlap between windows
    for start, end in _windows(text, lambda chunk: _fit(model, chunk)):
        chunk = text[start:end]
        if not chunk.strip():
            continue
        if _STOP_AT[0] and time.monotonic() > _STOP_AT[0]:
            raise OutOfTime(SLOW)
        for a, b, label, score in _predict(model, chunk):
            if label in MODEL_KIND and score >= KIND_THRESHOLD.get(MODEL_KIND[label], SPAN_THRESHOLD):
                kind = MODEL_KIND[label]
                for a2, b2, k in _pieces(text, start + a, start + b, kind):
                    out.setdefault((a2, b2, k, label), rules.Finding(a2, b2, k, f"model:{label}"))
    return list(out.values())


_FIELD_TAG = re.compile(r":?\d{2,3}[A-Z]?:|\d{1,3}=")  # SWIFT MT ":35E:" / FIX "54=" field tags


# a standard's name right before the number: RFC 5737, ISO 27001, ISO/IEC 27001, ICAO 9303, PEP 8
_STANDARD = re.compile(r"\b(?:RFC|ISO(?:/IEC)?|IEC|IEEE|ICAO|ECMA|PEP)[ -]?(?:Doc\.? )?$")


# a code constant right before a short number: SYS_FUTEX_WAIT = 5323. Its name must not contain a secret word anywhere
# (ADMIN_PIN = 4821, DB_APIKEY = 482193 stay masked), at the cost of constants like SYS_PKEY_FREE.
_CONSTANT = re.compile(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)[ \t]*[:=][ \t]*$")
_SECRET_IN_NAME = re.compile(r"KEY|TOKEN|SECRET|PASS|PWD|AUTH|PIN|OTP|CODE|CRED|SALT|SEED|PRIVATE")
# a GPG key id under a name that says so: GPG_KEY_ID = 'C105B9DE', "64bit key id" => '1054B7A24BD6EC30'
_GPG_ID = re.compile(r"(?i)(?:gpg|pgp)[\w ]*key[_ ]?id|\bkey[_ ]id\b|\bkeyid\b")


# A UI string under a key that names a message or a label, not a secret: "incorrectPassword": "Wrong user name or
# password", 'Reset Password' => 'Ailosod Cyfrinair'. The key must be a quoted label (a space, a final period or a
# capital first letter) or a message key around the secret word, and the value two or more words with no digits, so
# password: "correct horse battery staple" and ADMIN_PASSWORD=Summer Rain 2026 stay masked.
_MSG_WORDS = r"(?:Required|Failed|Title|Message|Error|Label|Placeholder|Hint|Text|Prompt|Description|Help|Tooltip|NotSupported)"
_SECRET_WORD = r"(?:[Pp]ass(?:word|phrase)?|[Tt]oken|[Aa]uth|[Ss]ecret|[Kk]ey|[Pp]in)"
_UI_KEY = (r"[A-Z][a-z]+(?: [\w-]+)*\.?|[a-z][\w-]*(?: [\w-]+)+\.?|[a-z]+\.|"  # 'Reset Password', 'Invalid key.'
           r"\w*" + _SECRET_WORD + r"\w*" + _MSG_WORDS + r"\w*|"  # passwordRequired, tokenAuthFailedTitle
           r"(?:incorrect|invalid|wrong|enter|forgot|join)\w*" + _SECRET_WORD + r"\w*")  # incorrectPassword
_UI_PAIR = re.compile(r"""(["'])(""" + _UI_KEY + r""")\1\s*(?:=>|:)\s*(["'])((?:(?!\3)[^\n\d])+)\3""")


def _ui_strings(text: str) -> list[tuple[int, int]]:
    out = []
    for m in _UI_PAIR.finditer(text):
        key, value = m.group(2), m.group(4)
        if re.search(_SECRET_WORD, key, re.I) and len(value.split()) >= 2:
            out.append(m.span(4))
    return out


_XAML_NAME = re.compile(r"\bx:(?:Key|Name|Uid)=[\"']$")  # <Color x:Key="CoolBlue">: a resource name, not a person


def _not_account(value: str) -> bool:
    """A short account match with no digits ('DDA', ':CRED') or a bare field tag ('77S', '62F'). A longer one with no
    digits (a member ID like MBR-ALPLCBLKIW) stays masked."""
    value = value.strip(":")
    return (not any(c.isdigit() for c in value) and len(value) <= 5) or re.fullmatch(r"\d{2,3}[A-Z]", value) is not None


_NEWLINE = re.compile(r"\n")
_NAME_SHAPE = re.compile(r"[A-Z][a-z'-]+(?: (?:[A-Z]\.|[A-Z][a-z'-]+)){1,3}")  # Tabea V. Thies
_SPLIT = re.compile(r"\n|[,;\t|]")
_SHAPE = {"PERSON": lambda v: any(c.isalpha() for c in v) and not any(c.isdigit() or c == "@" for c in v),
          "EMAIL": lambda v: re.fullmatch(r"[^\s,;<>]+@[^\s,;<>]+", v) is not None,
          "PHONE": lambda v: sum(c.isdigit() for c in v) >= 7 and re.fullmatch(r"[\d\s()+.\-]+", v) is not None}


# "STRIPE_KEY=", "api_key: ", "\"email\": " in front of a value. A colon counts only with space after it, or space
# or a quote before it, so "admin:hunter2" (a credential pair) and "https://" stay whole.
_KEY_PREFIX = re.compile(r"""[A-Za-z_][\w.\-]*(?:["']?[ \t]*=[ \t]*(?!=)|[ \t]*:[ \t]+|["'][ \t]*:[ \t]*|[ \t]+:[ \t]*)""")


def _unkey(text: str, a: int, b: int) -> tuple[int, int]:
    """Trim a model span to the value: the model sometimes starts a span at the key ("STRIPE_KEY=sk_...") or on the
    "=" ("=priya@..."). A masked key would be dropped by restore on write-back and corrupt the file. A span that
    would trim to nothing is kept whole: over-masking beats leaking."""
    a0, b0 = a, b
    while a < b:
        m = _KEY_PREFIX.match(text, a, b)
        if m and m.end() < b:
            a = m.end()
        elif text[a] in "=:" or text[a].isspace() or text[a] in _EDGE_PUNCT:
            a += 1
        else:
            break
    while b > a and (text[b - 1] == ":" or text[b - 1].isspace()):
        b -= 1
    return (a, b) if a < b else (a0, b0)


def _pieces(text: str, start: int, end: int, kind: str) -> list[tuple[int, int, str]]:
    """Clean up one model span. The model sometimes runs a span across rows (a CSV row's phone, city, the next rows,
    up to the next name): split such a span at line breaks and separators and keep the pieces that fit the kind.
    A piece that clearly has another kind's shape (a phone inside a runaway email span) takes that kind, so the
    right safe-value checks apply. Each piece is trimmed, then widened to whole words so a mask never leaves part
    of a value showing ("2PII_DATE")."""
    raw = text[start:end]
    parts = [(start, end)]
    trimmed = raw.strip().strip("".join(_EDGE_PUNCT))
    # names, emails and phones split at line breaks and separators; accounts and secrets at line breaks only; an
    # address or a date may rightly run over two lines
    split = _SPLIT if kind in _SHAPE else _NEWLINE
    if (kind not in ("ADDRESS", "DATE") and "\n" in raw) or (kind in _SHAPE and _SPLIT.search(raw) and not _SHAPE[kind](trimmed)):
        cuts = [start] + [start + m.end() for m in split.finditer(raw)]
        ends = [start + m.start() for m in split.finditer(raw)] + [end]
        parts = list(zip(cuts, ends))
    out = []
    for a, b in parts:
        while a < b and (text[a].isspace() or text[a] in _EDGE_PUNCT):
            a += 1
        while b > a and (text[b - 1].isspace() or text[b - 1] in _EDGE_PUNCT):
            b -= 1
        a, b = _unkey(text, a, b)
        if b <= a:
            continue
        piece = text[a:b]
        plain_words = not any(c.isdigit() or c == "@" for c in piece)
        # Over-masking beats leaking: a piece is dropped only when it is plain words that don't fit the kind
        # (a city cut out of a runaway name span); anything with digits or an @ stays masked whatever its label.
        if len(parts) > 1 and plain_words and kind in _SHAPE and (
                not _SHAPE[kind](piece) or (kind == "PERSON" and len(piece.split()) < 2)):
            continue
        tag = _FIELD_TAG.match(text, a, b) if kind != "DATE" else None  # "18:00" is a time, not a ":18:" field tag
        if tag and tag.end() < b:  # a SWIFT / FIX field tag in front of a value is not part of it
            a, piece = tag.end(), text[tag.end():b]
        k = kind
        if len(parts) > 1 and not (kind in _SHAPE and _SHAPE[kind](piece)):
            k = next((other for other in ("EMAIL", "PHONE") if _SHAPE[other](piece)), kind)
            if k == kind and kind != "PERSON" and _NAME_SHAPE.fullmatch(piece):
                k = "PERSON"  # a name on its own line inside a runaway account / secret match
        elif kind == "ACCOUNT" and _not_account(piece):
            continue  # only a whole model match is judged this way; a piece of a split keeps its mask
        while a > 0 and text[a - 1].isalnum() and text[a].isalnum():
            a -= 1
        while b < len(text) and text[b].isalnum() and text[b - 1].isalnum():
            b += 1
        out.append((a, b, k))
    return out


@dataclass
class Result:
    text: str                                      # masked text
    mode: str
    masked: list = field(default_factory=list)     # (placeholder, kind, source) - never the value
    skipped: list = field(default_factory=list)    # (kind, source, reason) - findings judged safe

    @property
    def changed(self) -> bool:
        return bool(self.masked)


def _merge(findings: list[rules.Finding]) -> list[rules.Finding]:
    """Keep non-overlapping findings, longest first; a keyed secret wins over an overlapping shape match. (Other keyed
    findings - an address or birth date under its key - don't: a longer model match covers a multi-line address.)"""
    ranked = sorted(findings, key=lambda f: (-(f.keyed and f.kind == "SECRET"), -(f.end - f.start), f.start))
    if any(f.end <= f.start for f in ranked):
        return _merge_slow(ranked)
    # the kept ones never overlap, so sorted by start they are sorted by end too: a new one can only touch the
    # one before it and the one after it (every finding against every kept one took seconds on a large CSV)
    starts: list[int] = []
    chosen: list[rules.Finding] = []
    for f in ranked:
        i = bisect_left(starts, f.start)
        if (i == 0 or chosen[i - 1].end <= f.start) and (i == len(starts) or f.end <= starts[i]):
            starts.insert(i, f.start)
            chosen.insert(i, f)
    return chosen


def _merge_slow(ranked: list[rules.Finding]) -> list[rules.Finding]:
    """_merge's rule, every finding against every kept one: for an empty finding, which the fast way can't place."""
    chosen: list[rules.Finding] = []
    for f in ranked:
        if all(f.end <= c.start or f.start >= c.end for c in chosen):
            chosen.append(f)
    return sorted(chosen, key=lambda f: f.start)


def _covered(spans: list[tuple[int, int]], starts: list[int], a: int, b: int) -> bool:
    """a..b lies inside one of spans (in order, not overlapping; starts: their first positions)."""
    i = bisect_right(starts, a) - 1
    return i >= 0 and b <= spans[i][1]


def _own_username(value: str) -> bool:
    """Your own login name: Claude Code already shows it to the model in the working folder (/Users/<you>/...),
    so masking it protects nothing and blocks any prompt holding a path. "mask_own_username": true masks it again."""
    import getpass
    from . import config
    try:
        return value.lower() == getpass.getuser().lower() and not config.load().get("mask_own_username")
    except Exception:
        return False


# Group columns that sit next to the owner in `ls -l` / `dir /q` output; the model can read "kai  staff" as a name.
_ACCOUNT_GROUPS = {"staff", "wheel", "admin", "admins", "everyone", "users", "root", "domain", "sudo", "adm",
                   "administrators", "builtin", "authority", "system", "nt"}


def _own_account(value: str) -> bool:
    """A model PERSON span made only of your login name and group words: the owner column of a file listing.
    A real name that merely starts with your login ("John Smith" for login john) still has another word, so stays
    masked."""
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9._-]+", value) if not w.isdigit()]  # link counts, sizes
    return any(_own_username(w) for w in words) and all(_own_username(w) or w in _ACCOUNT_GROUPS for w in words)


_PERSONAL_DATE = re.compile(
    r"(?i)(?:\b(?:born|birth|birthday|b-?day|dob|date of birth|age[ds]?|died|death|deceased|passed away|anniversary|"
    r"geboren|nacid[oa]|naci[óo]?|fecha de nacimiento|date de naissance)\b|\bd\.o\.b\b|\bgeb\.|\bn[ée]\(?e?\)? le\b)")
_GAP = re.compile(r"[\s,;:|()'\"\-\u2013\u2014]*")  # only punctuation between a name and its date


def _personal_date(text: str, start: int, end: int, names=()) -> bool:
    """A date the model found is personal when birth / death / age words are near it (60 characters before, 30 after),
    or when it belongs to a person's record: right next to a name with only punctuation between ("Vlad Popa
    (01/19/1998)", "Signed: Julien Moreau, 11/19/1977"), or in a CSV / table / SQL row that holds a name.
    Other dates (a deadline, "remind me to call Priya on 20 October", a log line) stay visible: in a prompt they
    would block it outright. "mask_all_dates": true masks every date again."""
    if _PERSONAL_DATE.search(text, max(0, start - 60), min(len(text), end + 30)):
        return True
    line_a, line_b = text.rfind("\n", 0, start) + 1, text.find("\n", end)
    line_b = len(text) if line_b < 0 else line_b
    row = sum(text.count(sep, line_a, line_b) for sep in (",", "|", ";", "\t")) >= 2
    for a, b in names:
        if not (line_a <= a and b <= line_b):
            continue
        gap = text[b:start] if b <= start else text[end:a]
        if row or _GAP.fullmatch(gap):
            return True
    from . import config
    try:
        return bool(config.load().get("mask_all_dates"))
    except Exception:
        return True  # can't read the setting: mask, the safe side


def _allowed(value: str, allow) -> bool:
    """User allowlist: exact values, or 're:<regex>' entries matched against the whole value."""
    for a in allow:
        if a.startswith("re:") and re.fullmatch(a[3:], value) or a == value:
            return True
    return False


def _always(text: str, always) -> list[rules.Finding]:
    """Your always-mask rules: exact values, or 're:<regex>' found anywhere. Keyed, so they win overlaps."""
    out = []
    for entry, kind in always:
        rx = entry[3:] if entry.startswith("re:") else re.escape(entry)
        for m in re.finditer(rx, text):
            if m.end() > m.start():
                out.append(rules.Finding(m.start(), m.end(), kind, "your-rule", keyed=True))
    return out


# OCR text: a run of 10+ digits, spaces or dashes allowed between them, is taken as an account or card number.
# OCR drops and adds spaces inside numbers ("4417 22901185 3306"), and the rules alone don't catch account numbers
# that fail the card checksum, so in OCR text these are masked without the usual safe-value judgement.
OCR_DIGITS = re.compile(r"(?<![\d.,])\d(?:[ -]?\d){9,}(?!\d)")


# Kinds whose stored values are looked for again in every later text, plain and encoded (decode.find_known);
# emails only as hashes and as a distinctive local part (decode.find_parts)
KNOWN_KINDS = {"SECRET", "PASSWORD", "CARD", "SSN", "IBAN", "ACCOUNT", "BANK"}
SEED_KINDS = KNOWN_KINDS | decode.HASH_ONLY


def known_values(vault: Vault | None, always=()) -> dict[str, str]:
    """{value: kind} for the session's known secrets (vault values of the kinds above, not copies derived from one)
    and your exact always-mask values. Never logged."""
    out = {}
    if vault is not None:
        for ph, v in vault.by_placeholder.items():
            kind = ph.split("_")[1]
            if kind in SEED_KINDS and len(v) >= decode.KNOWN_MIN and ph not in vault.derived:
                out[v] = kind
    for entry, kind in always:
        if not entry.startswith("re:") and len(entry) >= decode.KNOWN_MIN:
            out.setdefault(entry, kind)
    return out


def _known(text: str, known: dict) -> list[rules.Finding]:
    """A known secret seen again: plain keeps its placeholder; an encoded, hashed or partial copy keeps the kind but
    gets a placeholder of its own (source known-<how>), so restoring it puts that exact text back. Keyed, so they win
    overlaps."""
    return [rules.Finding(a, b, known[v], "known" if how == "plain" else "known-" + how.split("-")[0].lower(),
                          keyed=True)
            for a, b, v, how in decode.find_known(text, known) + decode.find_parts(text, known, _own_username)]


def _logged(text: str, files: dict) -> list[rules.Finding]:
    """Values the file log says Cloakroom restored into files this output may come from (filelog.known): exact
    matches, keyed, so they are masked even where detection would miss them."""
    out = []
    for v, kind in files.items():
        i = text.find(v)
        while i >= 0:
            out.append(rules.Finding(i, i + len(v), kind, "file-log", keyed=True))
            i = text.find(v, i + len(v))
    return out


class _Where:
    """What the checks below need to know about the text around a finding."""

    def __init__(self, text: str, found: list[rules.Finding]):
        self.text = text
        self.ui = _ui_strings(text)
        self.ui_starts = [a for a, _ in self.ui]
        self._found, self._people = found, None

    def people(self) -> list[tuple[int, int]]:
        """Where the people in the text are, worked out at the first model date."""
        if self._people is None:
            self._people = [(n.start, n.end) for n in self._found
                            if n.kind == "PERSON" and not _own_account(n.value(self.text))]
        return self._people


# Why a finding is safe where it stands, when its value alone doesn't say so. Each takes the finding and its
# surroundings and gives the reason, or nothing. _CONTEXT_CHECKS is their order: the first reason found is the one
# reported.

def _plain_date(f: rules.Finding, w: _Where):
    if f.kind == "DATE" and f.source.startswith("model:") and not _personal_date(w.text, f.start, f.end, w.people()):
        return "plain date (no birth, death or age words near it)"


def _own_name(f: rules.Finding, w: _Where):
    if (f.source == "home-dir-user" and _own_username(f.value(w.text))
            or f.kind == "PERSON" and _own_account(f.value(w.text))):
        return "your own username (already visible in the working folder)"


def _cut_short(f: rules.Finding, w: _Where):
    if f.kind in ("SECRET", "ACCOUNT") and not f.keyed and safe.ELLIPSIS.match(w.text, f.end) \
            and safe.reason(f.value(w.text) + "...", f.kind, f.keyed):
        return "cut short with ... (a placeholder, not a key)"  # "sk-...your-key-here...": the span stops at "sk-"


def _spec_number(f: rules.Finding, w: _Where):
    if f.kind == "ACCOUNT" and _STANDARD.search(w.text, max(0, f.start - 12), f.start) \
            and re.fullmatch(r"\d{1,5}(?:[-:]\d{1,4})?", f.value(w.text).strip()):
        return "standard or spec number (RFC 5737, ISO 27001)"


def _xaml_name(f: rules.Finding, w: _Where):
    if f.kind == "PERSON" and _XAML_NAME.search(w.text, max(0, f.start - 8), f.start):
        return "XAML resource name"


def _ui_text(f: rules.Finding, w: _Where):
    if f.kind in ("SECRET", "PERSON") and _covered(w.ui, w.ui_starts, f.start, f.end):
        return "UI text under a message or label key"


def _constant_number(f: rules.Finding, w: _Where):
    if f.kind in ("ACCOUNT", "SECRET") and not f.keyed and re.fullmatch(r"\d{1,6}", f.value(w.text)):
        c = _CONSTANT.search(w.text, max(0, f.start - 80), f.start)
        if c and not _SECRET_IN_NAME.search(c.group(1)):
            return "number given to a code constant"


def _gpg_id(f: rules.Finding, w: _Where):
    if f.kind in ("ACCOUNT", "SECRET") and not f.keyed \
            and re.fullmatch(r"[0-9A-Fa-f]{8}|[0-9A-Fa-f]{16}", f.value(w.text)) \
            and _GPG_ID.search(w.text, max(0, f.start - 40), f.start):
        return "GPG key id (public)"


def _test_card(f: rules.Finding, w: _Where):
    if f.kind in ("CARD", "ACCOUNT") and _digit_run(w.text, f) in safe.TEST_CARDS:
        return "published test card"


def _part_of_a_word(f: rules.Finding, w: _Where):
    """A PERSON finding that is part of a longer word: judged as that whole word."""
    if f.kind == "PERSON":
        a, b = f.start, f.end
        while a > 0 and w.text[a - 1].isalpha():
            a -= 1
        while b < len(w.text) and w.text[b].isalpha():
            b += 1
        if (a, b) != (f.start, f.end):
            return safe.reason(w.text[a:b], f.kind, f.keyed)


_CONTEXT_CHECKS = (_plain_date, _own_name, _cut_short, _spec_number, _xaml_name, _ui_text, _constant_number, _gpg_id,
                   _test_card, _part_of_a_word)


def _why_safe(f: rules.Finding, w: _Where, allow) -> str | None:
    """The reason a finding is not masked: on your allowlist, safe by its value (safe.reason), or safe where it
    stands (the checks above, in their order). Nothing when it is to be masked."""
    value = f.value(w.text)
    why = "on your allowlist" if _allowed(value, allow) else safe.reason(value, f.kind, f.keyed)
    for check in () if why else _CONTEXT_CHECKS:
        why = check(f, w)
        if why:
            break
    return why


def detect(text: str, mode: str = "full", allow=(), always=(), ocr: bool = False,
           known: dict | None = None, files: dict | None = None) -> tuple[list[rules.Finding], list[tuple]]:
    """Findings to mask, and findings skipped as safe (with the reason). ocr: the text came from OCR.
    known: {value: kind} of secrets already seen, matched plain and encoded (known_values).
    files: {value: kind} from the file log, matched plain."""
    if mode not in ("full", "rules"):
        raise ValueError("mode must be 'full' or 'rules'")
    found = rules.scan(text)
    if mode == "full":
        found += model_findings(text)
    own = [m.span() for m in rules.OWN_PLACEHOLDER.finditer(text)]
    own_starts = [a for a, _ in own]
    keep, skipped = [], []
    where = _Where(text, found)
    rule_verdict: dict = {}  # (start, end) -> (rule kind, skip reason or None); rules come before the model in found
    for f in _logged(text, files or {}) + found + _always(text, always) + _known(text, known or {}):
        if _covered(own, own_starts, f.start, f.end):
            continue
        if f.source in ("your-rule", "file-log") or f.source.startswith("known"):  # no safe-value judgement
            keep.append(f)
            continue
        if f.source.startswith("model:") and (f.start, f.end) in rule_verdict:
            # Same span as a rule finding: judge it as the rule's kind, so whether a value is safe doesn't depend on
            # which detector's label wins (a 555-01xx phone the model calls an account number)
            kind, rule_why = rule_verdict[(f.start, f.end)]
            if rule_why:
                skipped.append((f.kind, f.source, rule_why))
                continue
            f = rules.Finding(f.start, f.end, kind, f.source, f.keyed)
        why = _why_safe(f, where, allow)
        if not f.source.startswith("model:"):
            rule_verdict.setdefault((f.start, f.end), (f.kind, why))
        (skipped.append((f.kind, f.source, why)) if why else keep.append(f))
    if ocr:
        keep += [rules.Finding(m.start(), m.end(), "ACCOUNT", "ocr-digits") for m in OCR_DIGITS.finditer(text)
                 if not _covered(own, own_starts, m.start(), m.end())]
    keep = _unmerge_runs(text, keep)
    return _merge(keep + _repeats(text, keep)), skipped


def _unmerge_runs(text: str, keep: list[rules.Finding]) -> list[rules.Finding]:
    """A model ACCOUNT / SECRET span that runs over two or more kept rule findings ("415-820-4471 SSN 529-31-8846")
    would win the merge as the longest and hide the words between them under the wrong label. Drop it and keep the
    rule findings; a gap between them that holds a digit stays masked under the model's label, so nothing leaks."""
    by_rule = [f for f in keep if not f.source.startswith("model:")]
    out = []
    for f in keep:
        if not (f.source.startswith("model:") and f.kind in ("ACCOUNT", "SECRET")):
            out.append(f)
            continue
        inside = sorted((r for r in by_rule if f.start <= r.start and r.end <= f.end), key=lambda r: r.start)
        if len(inside) < 2:
            out.append(f)
            continue
        last = f.start
        for a, b in [(r.start, r.end) for r in inside] + [(f.end, f.end)]:
            if a > last and any(c.isdigit() for c in text[last:a]):
                g, h = last, a
                while g < h and (text[g].isspace() or text[g] in _EDGE_PUNCT):
                    g += 1
                while h > g and (text[h - 1].isspace() or text[h - 1] in _EDGE_PUNCT):
                    h -= 1
                out.append(rules.Finding(g, h, f.kind, f.source, f.keyed))
            last = max(last, b)
    return out


def _digit_run(text: str, f: rules.Finding) -> str:
    """Digits of the whole number around a finding, so a span cut short still matches a known value."""
    a, b = f.start, f.end
    while a > 0 and (text[a - 1].isdigit() or text[a - 1] in " -" and a > 1 and text[a - 2].isdigit()):
        a -= 1
    while b < len(text) and (text[b].isdigit() or text[b] in " -" and b + 1 < len(text) and text[b + 1].isdigit()):
        b += 1
    return re.sub(r"\D", "", text[a:b])


REPEAT_MIN = 4  # shorter values are left alone, to avoid masking common words
_REPEAT_SCANS = 4_000_000  # values x characters above which one pass over the text beats one search per value


def _longest_of(values: list[str], k: int = 0) -> str:
    """A pattern for what follows the first k characters of values (sorted, all different, the same up to k): at any
    position it matches the longest value that starts there. Shared starts are written once, so the text is read
    once however many values there are."""
    ends_here = len(values[0]) == k
    rest = values[1:] if ends_here else values
    branches, i = [], 0
    while i < len(rest):
        j = i
        while j + 1 < len(rest) and rest[j + 1][k] == rest[i][k]:
            j += 1
        first, last, same = rest[i], rest[j], k + 1  # sorted: what the first and last share, all between share
        while same < len(first) and same < len(last) and first[same] == last[same]:
            same += 1
        branches.append(re.escape(first[k:same]) + _longest_of(rest[i:j + 1], same))
        i = j + 1
    if not branches:
        return ""
    if len(branches) == 1 and not ends_here:
        return branches[0]
    return "(?:" + "|".join(branches) + ")" + ("?" if ends_here else "")


def _repeats(text: str, found: list[rules.Finding]) -> list[rules.Finding]:
    """Every other exact appearance of a value already judged sensitive (a username caught in one path
    still leaks where it repeats). Values under 4 characters are left alone to avoid masking common words."""
    wanted = {(f.value(text), f.kind) for f in found}
    wanted = [(v, k) for v, k in wanted if len(v) >= REPEAT_MIN]
    if len(wanted) * len(text) > _REPEAT_SCANS:
        try:
            return _repeats_one_pass(text, wanted)
        except (RecursionError, re.error, OverflowError):  # values nested too deep for one pattern
            pass
    return [rules.Finding(m.start(), m.end(), kind, "repeat")
            for value, kind in wanted for m in re.finditer(re.escape(value), text)]


def _repeats_one_pass(text: str, wanted: list[tuple[str, str]]) -> list[rules.Finding]:
    """_repeats for a customer list: thousands of values, each searched through the whole text, took seconds. Here
    the text is read once. Per value the same matches as one search each (left to right, not overlapping itself)."""
    kinds: dict[str, list[str]] = {}
    for value, kind in wanted:
        kinds.setdefault(value, []).append(kind)
    at_position = re.compile("(?=(" + _longest_of(sorted(kinds)) + "))")
    out, free, also = [], {}, {}  # free: where a value's next match may start; also: the values a value starts with
    for m in at_position.finditer(text):
        i, longest = m.start(), m.group(1)
        if longest not in also:
            also[longest] = [longest] + [longest[:n] for n in range(REPEAT_MIN, len(longest)) if longest[:n] in kinds]
        for value in also[longest]:
            if i >= free.get(value, 0):
                free[value] = i + len(value)
                out += [rules.Finding(i, i + len(value), kind, "repeat") for kind in kinds[value]]
    return out


def mask(text: str, vault: Vault | None = None, mode: str = "full", allow=(), always=(), ocr: bool = False,
         files: dict | None = None) -> Result:
    vault = vault if vault is not None else Vault()
    findings, skipped = detect(text, mode, allow, always, ocr, known_values(vault, always), files)
    out, last, masked = [], 0, []
    with vault.batch():  # one write of the session file for all of them (it was one per new value)
        for f in findings:
            ph = vault.placeholder(f.value(text), f.kind, derived=f.source.startswith("known-"))
            out += [text[last:f.start], ph]
            last = f.end
            masked.append((ph, f.kind, f.source))
    out.append(text[last:])
    return Result("".join(out), mode, masked, skipped)
