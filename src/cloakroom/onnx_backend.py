"""PII-Tracer on ONNX Runtime (CPU), without PyTorch at runtime.

Only the network (token logits) runs in ONNX. What the model's `predict()` does after the network is re-implemented
here in NumPy, matching the remote code at the pinned revision (modeling_pii_masking.py): the constrained BIOES
Viterbi decoder (with its two transition bias scalars), span assembly with per-span scores, and whitespace trimming.

The ONNX files come from `cloakroom export-onnx` / scripts/export_onnx.py (needs torch once) and live under the Cloakroom home.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config

FP32 = "model-fp32.onnx"
INT8 = "model-int8.onnx"
META = "decoder.json"
MAX_TOKENS = 4096  # the model's max_seq_len; predict() truncates there too


def model_dir() -> Path:
    from .engine import MODEL_REVISION
    return Path(os.environ.get("CLOAKROOM_ONNX_DIR") or config.HOME / "onnx" / MODEL_REVISION[:12])


def model_file(variant: str | None = None) -> Path:
    """CLOAKROOM_ONNX=fp32 (default: same spans as PyTorch fp32, ~9x faster than PyTorch on a CPU without bf16
    support) or int8 (about 10% faster again, ~95% span parity, a few more false alarms)."""
    variant = variant or os.environ.get("CLOAKROOM_ONNX", "fp32")
    if variant not in ("int8", "fp32"):
        raise ValueError("CLOAKROOM_ONNX must be 'int8' or 'fp32'")
    return model_dir() / (INT8 if variant == "int8" else FP32)


@dataclass
class PredictedSpan:
    start: int
    end: int
    label: str
    score: float


def _sigmoid(x: float) -> float:
    return float(1.0 / (1.0 + np.exp(-np.float32(x))))


class Decoder:
    """Constrained BIOES Viterbi, as ViterbiDecoder in the model's remote code."""

    def __init__(self, labels: list[str], b_bias: float = 0.0, e_bias: float = 0.0):
        self.labels = labels
        self.types = [lab[2:] for lab in labels if lab.startswith("S-")]
        l2i = {lab: i for i, lab in enumerate(labels)}
        n = len(labels)
        legal = np.zeros((n, n), dtype=bool)
        ends, begins = {l2i["O"]}, {l2i["O"]}
        for t in self.types:
            b, i, e, s = (l2i[f"{tag}-{t}"] for tag in "BIES")
            ends |= {e, s}
            begins |= {b, s}
            legal[b, i] = legal[b, e] = legal[i, i] = legal[i, e] = True
        for f in ends:
            for to in begins:
                legal[f, to] = True
        trans = np.where(legal, np.float32(0), np.float32(-np.inf)).astype(np.float32)
        for t in self.types:
            b, e = l2i[f"B-{t}"], l2i[f"E-{t}"]
            trans[:, b] += np.float32(b_bias)   # -inf stays -inf
            trans[e, :] += np.float32(e_bias)
        self.trans = trans
        self.start = np.array([0 if lab == "O" or lab[:2] in ("B-", "S-") else -np.inf for lab in labels], np.float32)
        self.end = np.array([0 if lab == "O" or lab[:2] in ("E-", "S-") else -np.inf for lab in labels], np.float32)

    def decode(self, logits: np.ndarray, offsets: list[tuple[int, int]], text: str | None = None) -> list[PredictedSpan]:
        logits = np.asarray(logits, dtype=np.float32)
        T, C = logits.shape
        back = np.zeros((T, C), dtype=np.int64)
        score = logits[0] + self.start
        for t in range(1, T):
            cand = score[:, None] + self.trans          # [prev, next]
            back[t] = cand.argmax(axis=0)
            score = logits[t] + cand[back[t], np.arange(C)]
        path = [int((score + self.end).argmax())]
        for t in range(T - 1, 0, -1):
            path.append(int(back[t, path[-1]]))
        path.reverse()
        spans = self._spans(path, logits, offsets)
        return _strip(text, spans) if text is not None else spans

    def _spans(self, path, logits, offsets) -> list[PredictedSpan]:
        out, T, t = [], len(path), 0
        while t < T:
            label = self.labels[path[t]]
            cs, ce = offsets[t]
            if label.startswith("S-"):
                if cs < ce:
                    out.append(PredictedSpan(cs, ce, label[2:], _sigmoid(logits[t, path[t]])))
                t += 1
            elif label.startswith("B-"):
                kind, end = label[2:], ce
                vals = [logits[t, path[t]]]
                t += 1
                while t < T:
                    inner = self.labels[path[t]]
                    if inner in (f"I-{kind}", f"E-{kind}"):
                        end = offsets[t][1]
                        vals.append(logits[t, path[t]])
                        t += 1
                        if inner[0] == "E":
                            break
                    else:
                        break
                if cs < end:
                    out.append(PredictedSpan(cs, end, kind, _sigmoid(np.mean(np.array(vals, np.float32)))))
            else:
                t += 1
        return out


def _strip(text: str, spans: list[PredictedSpan]) -> list[PredictedSpan]:
    out = []
    for s in spans:
        a, b = s.start, s.end
        while a < b and text[a] in " \t\n":
            a += 1
        while b > a and text[b - 1] in " \t\n":
            b -= 1
        if a < b:
            out.append(PredictedSpan(a, b, s.label, s.score))
    return out


class OnnxModel:
    """Same predict() contract as the PyTorch model: (spans, sensitivity). Sensitivity isn't exported: None."""

    def __init__(self, path: Path | None = None):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        path = Path(path) if path else model_file()
        meta_dir = path.parent
        if not path.exists():
            raise FileNotFoundError(f"{path} not found: run cloakroom export-onnx")
        self.path = path
        self.tokenizer = Tokenizer.from_file(str(meta_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(MAX_TOKENS)
        self.tokenizer.no_padding()
        meta = json.loads((meta_dir / META).read_text())
        self.decoder = Decoder(meta["labels"], meta["b_bias"], meta["e_bias"])
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])

    def logits(self, ids: list[int]) -> np.ndarray:
        x = np.array([ids], dtype=np.int64)
        return self.session.run(["logits"], {"input_ids": x, "attention_mask": np.ones_like(x)})[0][0]

    def predict(self, text: str):
        enc = self.tokenizer.encode(text)
        if not enc.ids:
            return [], 0.0
        return self.decoder.decode(self.logits(enc.ids), [tuple(o) for o in enc.offsets], text=text), None
