"""Detect, filter and mask. Two modes:

- full  (default): rules + PII-Tracer model. Fails closed: if the model can't load, check() raises ModelUnavailable.
- rules: rules only, labelled as such in every result.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import rules, safe
from .vault import Vault

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
    """full mode was asked for but the model can't run. Install cloakroom[model] or use mode='rules'."""


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
        if onnx_backend.model_file().exists():
            return "onnx"
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


def _torch_model():
    try:
        import torch
        from transformers import AutoModel
    except ImportError as e:
        raise ModelUnavailable("torch backend needs: pip install 'cloakroom[torch]'") from e
    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    return AutoModel.from_pretrained(MODEL_ID, revision=MODEL_REVISION, trust_remote_code=True).to(device).eval()


def model_findings(text: str) -> list[rules.Finding]:
    out = []
    for offset in range(0, max(len(text), 1), CHUNK_CHARS):
        chunk = text[offset:offset + CHUNK_CHARS]
        spans, _ = _model().predict(chunk)
        for s in spans:
            if s.label in MODEL_KIND and s.score >= KIND_THRESHOLD.get(MODEL_KIND[s.label], SPAN_THRESHOLD):
                start, end = offset + s.start, offset + s.end
                while start < end and (text[start].isspace() or text[start] in _EDGE_PUNCT):
                    start += 1
                while end > start and (text[end - 1].isspace() or text[end - 1] in _EDGE_PUNCT):
                    end -= 1
                if end > start:
                    out.append(rules.Finding(start, end, MODEL_KIND[s.label], f"model:{s.label}"))
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
    """Keep non-overlapping findings, longest first; a keyed finding wins over an overlapping shape match."""
    chosen: list[rules.Finding] = []
    for f in sorted(findings, key=lambda f: (-f.keyed, -(f.end - f.start), f.start)):
        if all(f.end <= c.start or f.start >= c.end for c in chosen):
            chosen.append(f)
    return sorted(chosen, key=lambda f: f.start)


def _allowed(value: str, allow) -> bool:
    """User allowlist: exact values, or 're:<regex>' entries matched against the whole value."""
    for a in allow:
        if a.startswith("re:") and re.fullmatch(a[3:], value) or a == value:
            return True
    return False


def detect(text: str, mode: str = "full", allow=()) -> tuple[list[rules.Finding], list[tuple]]:
    """Findings to mask, and findings skipped as safe (with the reason)."""
    if mode not in ("full", "rules"):
        raise ValueError("mode must be 'full' or 'rules'")
    found = rules.scan(text)
    if mode == "full":
        found += model_findings(text)
    own = [m.span() for m in rules.OWN_PLACEHOLDER.finditer(text)]
    keep, skipped = [], []
    for f in found:
        if any(s <= f.start and f.end <= e for s, e in own):
            continue
        why = "on your allowlist" if _allowed(f.value(text), allow) else safe.reason(f.value(text), f.kind, f.keyed)
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
        (skipped.append((f.kind, f.source, why)) if why else keep.append(f))
    return _merge(keep + _repeats(text, keep)), skipped


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


def mask(text: str, vault: Vault | None = None, mode: str = "full", allow=()) -> Result:
    vault = vault if vault is not None else Vault()
    findings, skipped = detect(text, mode, allow)
    out, last, masked = [], 0, []
    for f in findings:
        ph = vault.placeholder(f.value(text), f.kind)
        out += [text[last:f.start], ph]
        last = f.end
        masked.append((ph, f.kind, f.source))
    out.append(text[last:])
    return Result("".join(out), mode, masked, skipped)
