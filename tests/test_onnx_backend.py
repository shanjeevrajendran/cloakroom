"""ONNX backend: the NumPy decoder (no model needed), and span parity with PyTorch when the model is available."""
import os
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from cloakroom import onnx_backend as ob  # noqa: E402
from cloakroom.engine import MODEL_ID, MODEL_REVISION  # noqa: E402

TYPES = ["private_person", "private_email", "private_phone", "private_address", "private_url", "private_date",
         "account_number", "secret", "other_pii"]
LABELS = ["O"] + [f"{tag}-{t}" for t in TYPES for tag in "BIES"]
L = {lab: i for i, lab in enumerate(LABELS)}


def logits_for(tags, hi=6.0, lo=-6.0):
    x = np.full((len(tags), len(LABELS)), lo, dtype=np.float32)
    for t, tag in enumerate(tags):
        x[t, L[tag]] = hi
    return x


def test_single_and_multi_token_spans_with_scores():
    text = "Hi Ann Lee at x"
    offsets = [(0, 2), (2, 6), (6, 10), (10, 13), (13, 15)]
    x = logits_for(["O", "B-private_person", "E-private_person", "O", "S-secret"])
    x[2, L["E-private_person"]] = 2.0
    spans = ob.Decoder(LABELS).decode(x, offsets, text=text)
    assert [(s.label, text[s.start:s.end]) for s in spans] == [("private_person", "Ann Lee"), ("secret", "x")]
    assert spans[0].score == pytest.approx(1 / (1 + np.exp(-4.0)), abs=1e-6)   # sigmoid(mean(6, 2))
    assert spans[1].score == pytest.approx(1 / (1 + np.exp(-6.0)), abs=1e-6)


def test_constraints_forbid_illegal_paths():
    offsets = [(0, 1), (1, 2), (2, 3)]
    # E can't follow O: the decoder has to open the span with B (or drop it), never emit a lone E.
    spans = ob.Decoder(LABELS).decode(logits_for(["O", "O", "E-secret"]), offsets)
    assert all(s.end - s.start == 1 for s in spans)
    # B can't end the sequence: no span may run past the last token or stay open.
    spans = ob.Decoder(LABELS).decode(logits_for(["O", "O", "B-secret"]), offsets)
    assert all(s.end <= 3 and s.end - s.start == 1 for s in spans)


def test_bias_scalars_shift_transitions():
    d = ob.Decoder(LABELS, b_bias=-100.0)
    x = logits_for(["O", "B-secret", "E-secret"])
    assert [s.label for s in d.decode(x, [(0, 1), (1, 2), (2, 3)])] == []


def _remote_decoder():
    """The model's own ViterbiDecoder (remote code at the pinned revision), if torch and the snapshot are here."""
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    from huggingface_hub import hf_hub_download
    try:
        path = hf_hub_download(MODEL_ID, "modeling_pii_masking.py", revision=MODEL_REVISION, local_files_only=True)
    except Exception:
        pytest.skip("PII-Tracer snapshot not in the local Hugging Face cache")
    import importlib.util
    import sys
    if "pii_remote" not in sys.modules:
        spec = importlib.util.spec_from_file_location("pii_remote", path)
        sys.modules["pii_remote"] = mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    mod = sys.modules["pii_remote"]
    return mod


@pytest.mark.parametrize("seed", range(20))
def test_decoder_matches_remote_code_on_random_logits(seed):
    mod = _remote_decoder()
    import torch
    rng = np.random.default_rng(seed)
    T = int(rng.integers(1, 60))
    x = (rng.standard_normal((T, len(LABELS))) * 3).astype(np.float32)
    text = "".join(rng.choice(list("ab c\n"), size=2 * T))
    offsets = [(2 * t, 2 * t + 2) for t in range(T)]
    b, e = float(rng.normal()), float(rng.normal())
    want = mod.ViterbiDecoder(mod.BIOES_LABELS, b, e).decode(torch.tensor(x), offsets, text=text)
    got = ob.Decoder(LABELS, b, e).decode(x, offsets, text=text)
    assert [(s.label, s.start, s.end) for s in got] == [(s.label, s.start, s.end) for s in want]
    assert [s.score for s in got] == pytest.approx([s.score for s in want], abs=1e-6)


SYNTHETIC = [
    "Hi, I'm Daniel Whitfield, reach me at daniel.whitfield@fernhill.co or +44 7700 900123.",
    "DB_PASSWORD=Xk9vT2pLq8mN3r\nSTRIPE_SECRET_KEY=sk_live_9fQ2nV8rT1mK4pL7",
    "Ship to 14 Larch Avenue, Bristol BS3 4QT by 2026-11-03; account 12-34-56 87654321.",
    "def main():\n    return compute(42)  # nothing personal here",
]


def _onnx_dir() -> Path:
    return Path(os.environ.get("CLOAKROOM_ONNX_DIR") or Path.home() / ".cloakroom" / "onnx" / MODEL_REVISION[:12])


@pytest.fixture(scope="module")
def torch_fp32():
    _remote_decoder()
    if not (_onnx_dir() / ob.FP32).exists():
        pytest.skip("ONNX export not found: run scripts/export_onnx.py")
    import torch
    from transformers import AutoModel
    return AutoModel.from_pretrained(MODEL_ID, revision=MODEL_REVISION, trust_remote_code=True,
                                     dtype=torch.float32).eval()


def test_onnx_fp32_span_parity_with_torch(torch_fp32):
    m = ob.OnnxModel(_onnx_dir() / ob.FP32)
    for text in SYNTHETIC:
        want = [(s.label, s.start, s.end) for s in torch_fp32.predict(text)[0]]
        assert [(s.label, s.start, s.end) for s in m.predict(text)[0]] == want, text


PLANTED = {SYNTHETIC[0]: ["Daniel Whitfield", "daniel.whitfield@fernhill.co", "+44 7700 900123"],
           SYNTHETIC[1]: ["Xk9vT2pLq8mN3r", "sk_live_9fQ2nV8rT1mK4pL7"],
           SYNTHETIC[2]: ["14 Larch Avenue", "87654321"]}


@pytest.mark.parametrize("variant", ["int8", "fp32"])
def test_onnx_backend_masks_planted_values(variant, monkeypatch):
    """int8 spans can differ from PyTorch at the edges (about 95% exact parity on the corpus); what must hold is
    that nothing planted survives masking, and clean text is left alone."""
    if not (_onnx_dir() / ob.INT8).exists():
        pytest.skip("ONNX export not found: run scripts/export_onnx.py")
    from cloakroom import engine
    monkeypatch.setenv("CLOAKROOM_BACKEND", "onnx")
    monkeypatch.setenv("CLOAKROOM_ONNX", variant)
    monkeypatch.setenv("CLOAKROOM_ONNX_DIR", str(_onnx_dir()))
    monkeypatch.setattr(engine, "_MODEL", None)
    for text in SYNTHETIC:
        out = engine.mask(text, mode="full").text
        assert engine.BACKEND == "onnx"
        for value in PLANTED.get(text, []):
            assert value not in out, (variant, value)
        if text not in PLANTED:
            assert out == text
    monkeypatch.setattr(engine, "_MODEL", None)
