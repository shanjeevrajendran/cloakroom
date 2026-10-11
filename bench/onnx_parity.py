"""Span parity between the PyTorch backend and the ONNX backend (fp32 and int8) on a corpus.

    CORPUS=corpus.jsonl python bench/onnx_parity.py

A span matches when label, start and end are equal. Parity = matched / (torch spans + onnx spans - matched).
Reported for all spans the model emits, and for the spans Cloakroom acts on (score >= threshold, known label).
References: the torch backend as it runs on CPU (bf16 encoder, the checkpoint dtype) and torch in fp32.
Also times predict() per item with the model already loaded (no rules, no masking).
Needs cloakroom[torch] and the exported ONNX files (scripts/export_onnx.py).
"""
import json
import os
import time
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "src"))

from cloakroom import engine, onnx_backend  # noqa: E402


def spans(model, text, acted):
    out = set()
    for s in model.predict(text)[0]:
        if not acted or (s.score >= engine.SPAN_THRESHOLD and s.label in engine.MODEL_KIND):
            out.add((s.label, s.start, s.end))
    return out


def timed(model, corpus):
    t0 = time.perf_counter()
    for it in corpus:
        model.predict(it["text"])
    return round((time.perf_counter() - t0) * 1000 / len(corpus), 1)


def main():
    import torch
    from transformers import AutoModel

    corpus = [json.loads(l) for l in (HERE / os.environ.get("CORPUS", "corpus.jsonl")).read_text().splitlines()]
    refs = {"torch-cpu": engine._torch_model(),
            "torch-fp32": AutoModel.from_pretrained(engine.MODEL_ID, revision=engine.MODEL_REVISION,
                                                    trust_remote_code=True, dtype=torch.float32).eval()}
    models = {**refs, **{f"onnx-{v}": onnx_backend.OnnxModel(onnx_backend.model_file(v)) for v in ("fp32", "int8")}}
    report = {"ms_per_item": {}, "parity": {}}
    spans_of = {}
    for name, m in models.items():
        m.predict("warm up")
        report["ms_per_item"][name] = timed(m, corpus)
        spans_of[name] = {it["id"]: [spans(m, it["text"], a) for a in (False, True)] for it in corpus}
        print(f"{name:<11} {report['ms_per_item'][name]:>7} ms/item (model only, loaded)")
    for ref in refs:
        for name in models:
            if name == ref or name in refs and list(refs).index(name) < list(refs).index(ref):
                continue
            r = report["parity"][f"{name} vs {ref}"] = {}
            for k, scope in enumerate(("all", "acted")):
                same = only_ref = only_new = 0
                diffs = []
                for it in corpus:
                    a, b = spans_of[ref][it["id"]][k], spans_of[name][it["id"]][k]
                    same, only_ref, only_new = same + len(a & b), only_ref + len(a - b), only_new + len(b - a)
                    if a != b:
                        t = it["text"]
                        diffs.append({"id": it["id"], "ref_only": [(l, s, e, t[s:e]) for l, s, e in sorted(a - b)],
                                      "new_only": [(l, s, e, t[s:e]) for l, s, e in sorted(b - a)]})
                p = round(same / max(same + only_ref + only_new, 1), 4)
                r[scope] = {"same": same, "ref_only": only_ref, "new_only": only_new, "parity": p, "diffs": diffs}
                print(f"{name + ' vs ' + ref:<26} {scope:<6} same {same}, {ref} only {only_ref}, "
                      f"{name} only {only_new}, parity {p:.2%}")
    out = HERE / os.environ.get("CORPUS", "corpus.jsonl").replace(".jsonl", "_onnx_parity.json")
    out.write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
