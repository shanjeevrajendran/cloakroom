"""Read results that aren't plain text: PDFs, pictures, notebook figures.

Claude Code sends a PDF to the model as a document (or as page pictures with `pages`), and a picture as an image:
none of it is text the detectors can check. So a PDF becomes its text layer, extracted here and then masked like any
file, and a picture is held back with a note saying how to get the content another way. The replacement uses Read's
text shape: Claude Code accepts a result switched to `{"type": "text", ...}` and drops the document; a `pdf` result
with its data emptied, or a plain string, is ignored and the original PDF goes through (live test 2026-10-14).
"""
from __future__ import annotations

import base64
import io
import re
import unicodedata

TEXT_TYPES = {"text", "notebook", "file_unchanged"}  # Read results the string masking already covers
MIN_PAGE_CHARS = 20  # a page with less text than this is treated as having no text layer (a scan)

NO_TEXT = ("[Cloakroom held back this PDF: {why}, so it can't be checked for secrets or personal data. To work with "
           "it, run `pdftotext \"{path}\" -` (or `uvx --from pdfminer.six pdf2txt.py \"{path}\"`): command output is "
           "masked. A scanned PDF has no text to extract: ask the user to paste the part you need.]")
IMAGE = ("[Cloakroom held back this image: it can't check pictures for secrets or personal data. If the user wants "
         "you to see it, they can attach it to their own prompt.]")
FIGURE = "[figure held back by Cloakroom: pictures can't be checked]"
OTHER = "[Cloakroom held back this {kind} result: it isn't text, so it can't be checked.]"


def text_result(path: str, content: str) -> dict:
    """Read's text shape, which Claude Code accepts in place of any Read result."""
    n = content.count("\n") + 1
    return {"type": "text", "file": {"filePath": path, "content": content, "numLines": n, "startLine": 1,
                                     "totalLines": n}}


def _page_range(response: dict) -> range | None:
    """Pages a `parts` result covers (Read with `pages`), 0-based; None means all."""
    first, count = response.get("firstPage"), (response.get("file") or {}).get("count")
    if isinstance(first, int) and isinstance(count, int) and first >= 1:
        return range(first - 1, first - 1 + count)
    return None


def pdf_text(data: bytes, pages: range | None = None) -> tuple[str, int, int]:
    """The PDF's text layer page by page, how many pages had none, and how many pages. Raises if it can't be parsed."""
    from pypdf import PdfReader  # imported here so a missing pypdf holds the PDF back instead of breaking the hook
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        reader.decrypt("")  # an empty owner password is common; anything else raises and holds the PDF back
    idx = [i for i in (pages if pages is not None else range(len(reader.pages))) if 0 <= i < len(reader.pages)]
    out, empty = [], 0
    for i in idx:
        # NFKC: PDF text often carries ligatures ("ﬁ" for "fi") that would hide an email or a name from the detectors
        t = unicodedata.normalize("NFKC", reader.pages[i].extract_text() or "").strip()
        if len(re.sub(r"\s", "", t)) < MIN_PAGE_CHARS:
            empty += 1
            t = "[no text layer on this page: held back]"
        out.append(f"--- page {i + 1} ---\n{t}" if len(idx) > 1 else t)
    return "\n\n".join(out), empty, len(idx)


def _pdf(response: dict, path: str) -> tuple[dict, str]:
    f = response.get("file") or {}
    try:
        if response.get("type") == "pdf" and f.get("base64"):
            data = base64.b64decode(f["base64"])  # exactly what the model would have been sent
        else:
            with open(path, "rb") as fh:
                data = fh.read()
        text, empty, total = pdf_text(data, _page_range(response) if response.get("type") == "parts" else None)
    except ImportError:
        return text_result(path, NO_TEXT.format(why="its text reader (pypdf) isn't installed", path=path)), "held"
    except Exception as e:  # damaged, encrypted, unreadable: fail closed
        return text_result(path, NO_TEXT.format(why=f"it couldn't be read ({type(e).__name__})", path=path)), "held"
    if empty == total:  # no page with text (or no pages at all)
        return text_result(path, NO_TEXT.format(why="it has no text layer (a scan or pictures only)", path=path)), "held"
    return text_result(path, text), "pdf"


def _notebook(response: dict) -> tuple[dict, bool]:
    """Drop figures from notebook outputs; the text around them stays and is masked as usual."""
    held = False
    cells = (response.get("file") or {}).get("cells")
    if not isinstance(cells, list):
        return response, False
    new_cells = []
    for c in cells:
        if isinstance(c, dict) and isinstance(c.get("outputs"), list):
            outs = []
            for o in c["outputs"]:
                if isinstance(o, dict) and "image" in o:
                    o = {k: v for k, v in o.items() if k != "image"}
                    o["text"] = (str(o.get("text") or "") + "\n" + FIGURE).strip()
                    held = True
                outs.append(o)
            c = {**c, "outputs": outs}
        new_cells.append(c)
    return {**response, "file": {**response["file"], "cells": new_cells}}, held


def convert(response, path: str = "", allow_images: bool = False):
    """Turn a non-text Read result into something the masking can check.

    Returns (response, what): what is "" (unchanged), "pdf" (text layer extracted), "held" (held back),
    "figures" (notebook pictures dropped) or "image-allowed" (passed by the allow_images setting)."""
    if not isinstance(response, dict) or not isinstance(response.get("file"), dict):
        return response, ""
    kind = response.get("type")
    path = response["file"].get("filePath") or path  # an image result carries no filePath
    if kind in ("pdf", "parts"):
        return _pdf(response, path)
    if kind == "notebook":
        if allow_images:
            return response, ""
        new, held = _notebook(response)
        return (new, "figures") if held else (response, "")
    if kind is None or kind in TEXT_TYPES:
        return response, ""
    if kind == "image":
        return (response, "image-allowed") if allow_images else (text_result(path, IMAGE), "held")
    return text_result(path, OTHER.format(kind=str(kind or "unknown"))), "held"  # a Read result type we don't know
