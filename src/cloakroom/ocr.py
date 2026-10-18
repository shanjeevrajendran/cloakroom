"""Local OCR for scanned PDF pages and pictures (the optional `cloakroom[ocr]` extra).

RapidOCR runs the PP-OCR models through ONNX Runtime on the CPU; the models ship inside its wheel, and they are
passed by path so a missing file raises (and the read is held back) instead of being downloaded. PDF pages are
rendered with pypdfium2, so there is no poppler or other system program. Nothing leaves the machine.
Without the extra, `available()` is False and Read results are held back exactly as before.
"""
from __future__ import annotations

import io
import threading
import time
from pathlib import Path

RENDER_SCALE = 2.0  # 144 dpi: small print stays readable, a page renders in well under a second
_ENGINE = []


class OCRError(RuntimeError):
    pass


def available() -> bool:
    from . import winlibs
    winlibs.add_dll_dirs()
    try:
        import pypdfium2  # noqa: F401
        import rapidocr  # noqa: F401
        return True
    except Exception:
        return False


def _engine():
    if not _ENGINE:
        import logging

        from . import winlibs
        winlibs.add_dll_dirs()
        import rapidocr
        from rapidocr import RapidOCR
        models = Path(rapidocr.__file__).parent / "models"
        paths = {"Det.model_path": models / "PP-OCRv6_det_small.onnx",
                 "Cls.model_path": models / "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
                 "Rec.model_path": models / "PP-OCRv6_rec_small.onnx"}
        missing = [p.name for p in paths.values() if not p.is_file()]
        if missing:  # never let RapidOCR fetch them at run time
            raise OCRError(f"OCR model files missing: {', '.join(missing)}")
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        _ENGINE.append(RapidOCR(params={**{k: str(v) for k, v in paths.items()}, "Global.log_level": "error"}))
    return _ENGINE[0]


def _lines(result) -> str:
    """OCR boxes back into lines of text: boxes whose vertical centres are close sit on one line, left to right."""
    boxes, txts = getattr(result, "boxes", None), getattr(result, "txts", None)
    if boxes is None or not txts:
        return ""
    items = []
    for box, t in zip(boxes, txts):
        ys, xs = [p[1] for p in box], [p[0] for p in box]
        items.append(((min(ys) + max(ys)) / 2, max(ys) - min(ys), min(xs), str(t)))
    items.sort()
    lines, cur = [], []
    for it in items:
        if cur and abs(it[0] - cur[-1][0]) > max(it[1], cur[-1][1]) / 2:
            lines.append(cur)
            cur = []
        cur.append(it)
    if cur:
        lines.append(cur)
    return "\n".join(" ".join(i[3] for i in sorted(line, key=lambda i: i[2])) for line in lines).strip()


def _run(fn, timeout: float):
    """fn() with a time limit; raises OCRError on timeout or failure (the caller holds the read back)."""
    out = {}

    def work():
        try:
            out["v"] = fn()
        except BaseException as e:  # noqa: BLE001
            out["e"] = e
    t = threading.Thread(target=work, daemon=True)  # a hook process exits when done, taking a stuck thread along
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise OCRError(f"OCR took longer than {timeout:g} s")
    if "e" in out:
        raise OCRError(f"OCR failed ({type(out['e']).__name__})") from out["e"]
    return out["v"]


def image_text(data: bytes, timeout: float = 30) -> tuple[str, float]:
    """Text in a picture (PNG, JPEG, ...) and the milliseconds it took."""
    t0 = time.monotonic()

    def go():
        import numpy as np
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        return _lines(_engine()(np.array(im)[:, :, ::-1]))  # RapidOCR expects OpenCV's BGR order
    return _run(go, timeout), (time.monotonic() - t0) * 1000


def pdf_pages_text(data: bytes, pages: list[int], timeout: float = 30) -> tuple[dict[int, str], float]:
    """Text of the given 0-based PDF pages, rendered and read; the whole batch shares one time limit."""
    t0 = time.monotonic()

    def go():
        import numpy as np
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(data)
        try:
            out = {}
            for i in pages:
                im = doc[i].render(scale=RENDER_SCALE).to_pil().convert("RGB")
                out[i] = _lines(_engine()(np.array(im)[:, :, ::-1]))
            return out
        finally:
            doc.close()
    return _run(go, timeout), (time.monotonic() - t0) * 1000
