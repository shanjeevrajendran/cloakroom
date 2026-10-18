"""Export PII-Tracer's network (token logits only) to ONNX, then quantize the encoder to int8.

Needs the torch extra once: pip install 'cloakroom[model,torch]'

    cloakroom export-onnx [--out DIR]      (same as: python scripts/export_onnx.py)

Writes model-fp32.onnx (+ .data, weights over 2 GB) and model-int8.onnx into DIR, by default the Cloakroom home
(~/.cloakroom/onnx/<revision>), where the onnx backend looks for them. Nothing is uploaded anywhere.
Decoding (constrained BIOES Viterbi, span assembly, scores) is not exported: see cloakroom/onnx_backend.py.
"""
from __future__ import annotations

import pathlib


def export(final: pathlib.Path, int8: bool = True) -> None:
    """Build into a temporary folder and move it into place only when the fp32 model, tokenizer and metadata are all
    there: a build killed half-way (out of memory on an 8 GB machine) must never leave a model that can't load.
    The optional int8 copy comes after, in place, written under a temporary name."""
    import shutil

    from . import winlibs
    winlibs.add_dll_dirs()
    import torch
    from transformers import AutoModel

    from . import onnx_backend
    from .engine import MODEL_ID, MODEL_REVISION

    out = final.parent / (final.name + ".building")
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    model = AutoModel.from_pretrained(MODEL_ID, revision=MODEL_REVISION, trust_remote_code=True,
                                      dtype=torch.float32, attn_implementation="eager").eval()

    class Logits(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids, attention_mask):
            return self.m(input_ids=input_ids, attention_mask=attention_mask).logits

    ids = torch.tensor([[9707, 11, 358, 2776, 13509, 26350, 2566, 13]])
    fp32 = out / onnx_backend.FP32
    for old in (fp32, out / (fp32.name + ".data"), out / onnx_backend.INT8):  # onnx.save appends to a .data file
        old.unlink(missing_ok=True)
    torch.onnx.export(Logits(model), (ids, torch.ones_like(ids)), str(fp32), dynamo=False,
                      input_names=["input_ids", "attention_mask"], output_names=["logits"],
                      dynamic_axes={"input_ids": {0: "batch", 1: "seq"}, "attention_mask": {0: "batch", 1: "seq"},
                                    "logits": {0: "batch", 1: "seq"}},
                      opset_version=17)
    import onnx
    m = onnx.load(str(fp32))  # re-save with all weights in one external file next to the graph
    onnx.save(m, str(fp32), save_as_external_data=True, all_tensors_to_one_file=True,
              location=fp32.name + ".data", size_threshold=1024)
    for p in out.iterdir():  # the exporter's per-tensor files
        if p.name not in (fp32.name, fp32.name + ".data") and not p.suffix == ".onnx" and p.is_file():
            p.unlink()
    labels, b_bias, e_bias = model.viterbi.labels, float(model.viterbi.b_bias), float(model.viterbi.e_bias)
    del model, m  # quantizing holds several copies of the graph: free the torch weights first (CI runners have 7-16 GB)
    import gc
    gc.collect()
    # what the NumPy decoder needs besides the network: the tokenizer and the two Viterbi bias scalars
    import json
    from huggingface_hub import hf_hub_download
    shutil.copyfile(hf_hub_download(MODEL_ID, "tokenizer.json", revision=MODEL_REVISION), out / "tokenizer.json")
    (out / onnx_backend.META).write_text(json.dumps({
        "model": MODEL_ID, "revision": MODEL_REVISION, "labels": labels, "b_bias": b_bias, "e_bias": e_bias}))
    shutil.rmtree(final, ignore_errors=True)
    out.rename(final)
    fp32 = final / onnx_backend.FP32
    print(f"wrote {fp32}", flush=True)
    if int8:
        tmp = final / (onnx_backend.INT8 + ".tmp")
        quantize(fp32, tmp)
        tmp.rename(final / onnx_backend.INT8)
        print(f"wrote {final / onnx_backend.INT8}")


def quantize(fp32: pathlib.Path, int8: pathlib.Path) -> None:
    """Dynamic int8 (per-channel weights) for the encoder's matmuls. Kept fp32: the token head (fp32 in the
    checkpoint) and every MLP down_proj, whose inputs carry activation outliers (up to ~6500x the mean in layers 10
    and 19) that per-tensor dynamic activation quantization can't represent: quantizing them drops span parity with
    fp32 from 94% to 75% on the corpus."""
    import onnx
    from onnxruntime.quantization import QuantType, quantize_dynamic

    graph = onnx.load(str(fp32), load_external_data=False).graph
    keep = [n.name for n in graph.node if n.op_type == "MatMul" and ("token_cls_head" in n.name or "down_proj" in n.name)]
    quantize_dynamic(str(fp32), str(int8), weight_type=QuantType.QInt8, op_types_to_quantize=["MatMul"],
                     nodes_to_exclude=keep, per_channel=True)
