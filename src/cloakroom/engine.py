"""Detect, filter and mask. Two modes:

- full  (default): rules + PII-Tracer model. Fails closed: if the model can't load, check() raises ModelUnavailable.
- rules: rules only, labelled as such in every result.
"""
from __future__ import annotations

import os
import re

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
CHUNK_CHARS = 6000     # model truncates at 4096 tokens
MODEL_KIND = {"private_person": "PERSON", "private_email": "EMAIL", "private_phone": "PHONE",
              "private_address": "ADDRESS", "private_url": "URL", "private_date": "DATE",
              "account_number": "ACCOUNT", "secret": "SECRET"}


_EDGE_PUNCT = ",;\"'`"  # model spans sometimes take the separator next to a value


class ModelUnavailable(RuntimeError):
    """full mode was asked for but the model can't run. Install cloakroom[model,torch] or use mode='rules'."""


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


def model_findings(text: str) -> list[rules.Finding]:
    out = []
    for offset in range(0, max(len(text), 1), CHUNK_CHARS):
        chunk = text[offset:offset + CHUNK_CHARS]
        spans, _ = _model().predict(chunk)
        for s in spans:
            if s.label in MODEL_KIND and s.score >= KIND_THRESHOLD.get(MODEL_KIND[s.label], SPAN_THRESHOLD):
                kind = MODEL_KIND[s.label]
                for start, end, k in _pieces(text, offset + s.start, offset + s.end, kind):
                    out.append(rules.Finding(start, end, k, f"model:{s.label}"))
    return out


_FIELD_TAG = re.compile(r":?\d{2,3}[A-Z]?:|\d{1,3}=")  # SWIFT MT ":35E:" / FIX "54=" field tags


# a standard's name right before the number: RFC 5737, ISO 27001, ISO/IEC 27001, ICAO 9303, PEP 8
_STANDARD = re.compile(r"\b(?:RFC|ISO(?:/IEC)?|IEC|IEEE|ICAO|ECMA|PEP)[ -]?(?:Doc\.? )?$")


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
    chosen: list[rules.Finding] = []
    for f in sorted(findings, key=lambda f: (-(f.keyed and f.kind == "SECRET"), -(f.end - f.start), f.start)):
        if all(f.end <= c.start or f.start >= c.end for c in chosen):
            chosen.append(f)
    return sorted(chosen, key=lambda f: f.start)


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


# Kinds whose stored values are looked for again in every later text, plain and encoded (decode.find_known)
KNOWN_KINDS = {"SECRET", "PASSWORD", "CARD", "SSN", "IBAN", "ACCOUNT"}


def known_values(vault: Vault | None, always=()) -> dict[str, str]:
    """{value: kind} for the session's known secrets (vault values of the kinds above) and your exact always-mask
    values. Never logged."""
    out = {}
    if vault is not None:
        for ph, v in vault.by_placeholder.items():
            kind = ph.split("_")[1]
            if kind in KNOWN_KINDS and len(v) >= decode.KNOWN_MIN:
                out[v] = kind
    for entry, kind in always:
        if not entry.startswith("re:") and len(entry) >= decode.KNOWN_MIN:
            out.setdefault(entry, kind)
    return out


def _known(text: str, known: dict) -> list[rules.Finding]:
    """A known secret seen again: plain keeps its kind (and its placeholder); an encoded form is a SECRET of its own,
    so restoring it puts the encoded text back as it was. Keyed, so they win overlaps."""
    return [rules.Finding(a, b, known[v] if how == "plain" else "SECRET",
                          "known" if how == "plain" else "known-" + how.split("-")[0].lower(), keyed=True)
            for a, b, v, how in decode.find_known(text, known)]


def detect(text: str, mode: str = "full", allow=(), always=(), ocr: bool = False,
           known: dict | None = None) -> tuple[list[rules.Finding], list[tuple]]:
    """Findings to mask, and findings skipped as safe (with the reason). ocr: the text came from OCR.
    known: {value: kind} of secrets already seen, matched plain and encoded (known_values)."""
    if mode not in ("full", "rules"):
        raise ValueError("mode must be 'full' or 'rules'")
    found = rules.scan(text)
    if mode == "full":
        found += model_findings(text)
    own = [m.span() for m in rules.OWN_PLACEHOLDER.finditer(text)]
    keep, skipped = [], []
    rule_verdict: dict = {}  # (start, end) -> (rule kind, skip reason or None); rules come before the model in found
    for f in found + _always(text, always) + _known(text, known or {}):
        if any(s <= f.start and f.end <= e for s, e in own):
            continue
        if f.source == "your-rule" or f.source.startswith("known"):  # you said mask it: no safe-value judgement
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
        why = "on your allowlist" if _allowed(f.value(text), allow) else safe.reason(f.value(text), f.kind, f.keyed)
        if not why and f.kind == "DATE" and f.source.startswith("model:") and not _personal_date(
                text, f.start, f.end, [(n.start, n.end) for n in found
                                   if n.kind == "PERSON" and not _own_account(n.value(text))]):
            why = "plain date (no birth, death or age words near it)"
        if not why and (f.source == "home-dir-user" and _own_username(f.value(text))
                        or f.kind == "PERSON" and _own_account(f.value(text))):
            why = "your own username (already visible in the working folder)"
        if not why and f.kind in ("SECRET", "ACCOUNT") and not f.keyed and safe.ELLIPSIS.match(text, f.end) \
                and safe.reason(f.value(text) + "...", f.kind, f.keyed):
            why = "cut short with ... (a placeholder, not a key)"  # "sk-...your-key-here...": the span stops at "sk-"
        if not why and f.kind == "ACCOUNT" and _STANDARD.search(text, max(0, f.start - 12), f.start) \
                and re.fullmatch(r"\d{1,5}(?:[-:]\d{1,4})?", f.value(text).strip()):
            why = "standard or spec number (RFC 5737, ISO 27001)"
        if not why and f.kind in ("CARD", "ACCOUNT") and _digit_run(text, f) in safe.TEST_CARDS:
            why = "published test card"
        if not why and f.kind == "PERSON":
            a, b = f.start, f.end
            while a > 0 and text[a - 1].isalpha():
                a -= 1
            while b < len(text) and text[b].isalpha():
                b += 1
            if (a, b) != (f.start, f.end):
                why = safe.reason(text[a:b], f.kind, f.keyed)
        if not f.source.startswith("model:"):
            rule_verdict.setdefault((f.start, f.end), (f.kind, why))
        (skipped.append((f.kind, f.source, why)) if why else keep.append(f))
    if ocr:
        keep += [rules.Finding(m.start(), m.end(), "ACCOUNT", "ocr-digits") for m in OCR_DIGITS.finditer(text)
                 if not any(s <= m.start() and m.end() <= e for s, e in own)]
    keep = _unmerge_runs(text, keep)
    return _merge(keep + _repeats(text, keep)), skipped


def _unmerge_runs(text: str, keep: list[rules.Finding]) -> list[rules.Finding]:
    """A model ACCOUNT / SECRET span that runs over two or more kept rule findings ("415-820-4471 SSN 529-31-8846")
    would win the merge as the longest and hide the words between them under the wrong label. Drop it and keep the
    rule findings; a gap between them that holds a digit stays masked under the model's label, so nothing leaks."""
    by_rule = [f for f in keep if not f.source.startswith("model:")]
    out = []
    for f in keep:
        inside = sorted((r for r in by_rule if f.start <= r.start and r.end <= f.end), key=lambda r: r.start)
        if not (f.source.startswith("model:") and f.kind in ("ACCOUNT", "SECRET") and len(inside) >= 2):
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


def _repeats(text: str, found: list[rules.Finding]) -> list[rules.Finding]:
    """Every other exact appearance of a value already judged sensitive (a username caught in one path
    still leaks where it repeats). Values under 4 characters are left alone to avoid masking common words."""
    out = []
    for value, kind in {(f.value(text), f.kind) for f in found}:
        if len(value) < 4:
            continue
        for m in re.finditer(re.escape(value), text):
            out.append(rules.Finding(m.start(), m.end(), kind, "repeat"))
    return out


def mask(text: str, vault: Vault | None = None, mode: str = "full", allow=(), always=(), ocr: bool = False) -> Result:
    vault = vault if vault is not None else Vault()
    findings, skipped = detect(text, mode, allow, always, ocr, known_values(vault, always))
    out, last, masked = [], 0, []
    for f in findings:
        ph = vault.placeholder(f.value(text), f.kind)
        out += [text[last:f.start], ph]
        last = f.end
        masked.append((ph, f.kind, f.source))
    out.append(text[last:])
    return Result("".join(out), mode, masked, skipped)
