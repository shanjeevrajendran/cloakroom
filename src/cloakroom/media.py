"""Read results that aren't plain text: PDFs, pictures, notebook figures.

Claude Code sends a PDF to the model as a document (or as page pictures with `pages`), and a picture as an image:
none of it is text the detectors can check. So a PDF becomes its text layer, extracted here and then masked like any
file. With the `ocr` extra (ocr.py), scanned pages and pictures are read locally and their text is masked the same
way; without it, or when OCR fails or runs out of time, they are held back with a note saying how to get the content
another way. The replacement uses Read's
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
OCR_NOTE = "[Text read from this {what} by Cloakroom's local OCR; the {what} itself was not sent. OCR can misread.]\n"
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


class Ocr:
    """OCR settings for one Read, and what it did (for the hook log)."""

    def __init__(self, on: bool = False, max_pages: int = 20, timeout: float = 30):
        self.on, self.max_pages, self.timeout = on, max_pages, timeout
        self.pages, self.ms, self.error = 0, 0.0, ""

    @classmethod
    def from_config(cls, cfg: dict) -> "Ocr":
        from . import ocr
        return cls(bool(cfg.get("ocr", True)) and ocr.available(), int(cfg.get("ocr_max_pages", 20)),
                   float(cfg.get("ocr_timeout", 30)))


def pdf_text(data: bytes, pages: range | None = None, ocr: Ocr | None = None) -> tuple[str, int, int]:
    """The PDF's text layer page by page, how many pages had none, and how many pages. Raises if it can't be parsed.
    With OCR on, pages without a text layer (up to ocr.max_pages) are rendered and read instead; if OCR fails they
    stay held back, as without it."""
    from pypdf import PdfReader  # imported here so a missing pypdf holds the PDF back instead of breaking the hook
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        reader.decrypt("")  # an empty owner password is common; anything else raises and holds the PDF back
    idx = [i for i in (pages if pages is not None else range(len(reader.pages))) if 0 <= i < len(reader.pages)]
    # NFKC: PDF text often carries ligatures ("ﬁ" for "fi") that would hide an email or a name from the detectors
    texts = {i: unicodedata.normalize("NFKC", reader.pages[i].extract_text() or "").strip() for i in idx}
    scans = [i for i in idx if len(re.sub(r"\s", "", texts[i])) < MIN_PAGE_CHARS]
    read = {}
    # an encrypted PDF is never rendered: it keeps the 0.2.4 behaviour (text layer, else held back)
    if ocr and ocr.on and scans and not reader.is_encrypted:
        from . import ocr as engine
        try:
            read, ocr.ms = engine.pdf_pages_text(data, scans[:ocr.max_pages], ocr.timeout)
        except Exception as e:  # OCR missing, failed or too slow: the pages stay held back
            ocr.error = str(e) or type(e).__name__
        read = {i: unicodedata.normalize("NFKC", t).strip() for i, t in read.items()
                if len(re.sub(r"\s", "", t)) >= 2}  # a blank page reads as nothing: still held back
        ocr.pages = len(read)
    out, empty = [], 0
    for i in idx:
        t = texts[i]
        if i in read:
            t = OCR_NOTE.format(what="page") + read[i]
        elif i in scans:
            empty += 1
            t = "[no text layer on this page: held back]"
        out.append(f"--- page {i + 1} ---\n{t}" if len(idx) > 1 else t)
    return "\n\n".join(out), empty, len(idx)


def _pdf(response: dict, path: str, ocr: Ocr | None = None) -> tuple[dict, str]:
    f = response.get("file") or {}
    try:
        if response.get("type") == "pdf" and f.get("base64"):
            data = base64.b64decode(f["base64"])  # exactly what the model would have been sent
        else:
            with open(path, "rb") as fh:
                data = fh.read()
        text, empty, total = pdf_text(data, _page_range(response) if response.get("type") == "parts" else None, ocr)
    except ImportError:
        return text_result(path, NO_TEXT.format(why="its text reader (pypdf) isn't installed", path=path)), "held"
    except Exception as e:  # damaged, encrypted, unreadable: fail closed
        return text_result(path, NO_TEXT.format(why=f"it couldn't be read ({type(e).__name__})", path=path)), "held"
    if empty == total:  # no page with text (or no pages at all)
        return text_result(path, NO_TEXT.format(why="it has no text layer (a scan or pictures only)", path=path)), "held"
    return text_result(path, text), ("ocr" if ocr and ocr.pages else "pdf")


def _image(response: dict, path: str, ocr: Ocr) -> tuple[dict, str]:
    """A picture's text by local OCR, or held back (no OCR, unreadable, too slow, or no text in it)."""
    from . import ocr as engine
    try:
        text, ocr.ms = engine.image_text(base64.b64decode(response["file"]["base64"]), ocr.timeout)
    except Exception as e:
        ocr.error = str(e) or type(e).__name__
        return text_result(path, IMAGE), "held"
    text = unicodedata.normalize("NFKC", text).strip()
    if len(re.sub(r"\s", "", text)) < 2:
        return text_result(path, IMAGE), "held"
    ocr.pages = 1
    return text_result(path, OCR_NOTE.format(what="image") + text), "ocr"


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


def convert(response, path: str = "", allow_images: bool = False, ocr: Ocr | None = None):
    """Turn a non-text Read result into something the masking can check.

    Returns (response, what): what is "" (unchanged), "pdf" (text layer extracted), "ocr" (text read by OCR, from
    scanned pages or a picture), "held" (held back), "figures" (notebook pictures dropped) or "image-allowed"
    (passed by the allow_images setting)."""
    if not isinstance(response, dict) or not isinstance(response.get("file"), dict):
        return response, ""
    kind = response.get("type")
    path = response["file"].get("filePath") or path  # an image result carries no filePath
    if kind in ("pdf", "parts"):
        return _pdf(response, path, ocr)
    if kind == "notebook":
        if allow_images:
            return response, ""
        new, held = _notebook(response)
        return (new, "figures") if held else (response, "")
    if kind is None or kind in TEXT_TYPES:
        return response, ""
    if kind == "image":
        if allow_images:
            return response, "image-allowed"
        if ocr and ocr.on and response["file"].get("base64"):
            return _image(response, path, ocr)
        return text_result(path, IMAGE), "held"
    return text_result(path, OTHER.format(kind=str(kind or "unknown"))), "held"  # a Read result type we don't know


# --- MCP tool results -------------------------------------------------------------------------------------------
# An MCP tool (a browser or desktop screenshot tool, say) returns a list of content blocks; an image block carries
# the picture as base64: MCP's {"type": "image", "data", "mimeType"}, or the API's {"type": "image", "source":
# {"data", "media_type"}}, or an embedded {"type": "resource", "resource": {"blob", "mimeType": "image/..."}}.
# Each one becomes a text block holding its OCR text (masked afterwards like any output), or a held-back note.
# The pixels are never passed on, unless allow_images is on.
MCP_IMAGE = "[Cloakroom held back an image from this tool: {why}, so it can't be checked for secrets or personal data.]"


def _mcp_image(block: dict) -> tuple[bool, str | None]:
    """(is an image block, its base64 data or None when it can't be found)."""
    kind = block.get("type")
    if kind == "image":
        src = block.get("source")
        data = block.get("data") if "data" in block else (src.get("data") if isinstance(src, dict) else None)
        return True, data if isinstance(data, str) and data else None
    if kind == "resource" and isinstance(block.get("resource"), dict):
        res = block["resource"]
        if str(res.get("mimeType", "")).lower().startswith("image/"):
            return True, res.get("blob") if isinstance(res.get("blob"), str) and res.get("blob") else None
    if kind == "image_url" or (str(block.get("mimeType", "")).lower().startswith("image/")
                               and ("data" in block or "blob" in block)):  # a resource_link (a URI) has no pixels
        data = block.get("data") or block.get("blob")
        return True, data if isinstance(data, str) and data else None
    return False, None


def _mcp_ocr(data: str, ocr: Ocr | None) -> tuple[str, bool]:
    """Text for one MCP image block, and whether it holds OCR text (False: held back)."""
    if not (ocr and ocr.on):
        return MCP_IMAGE.format(why="local OCR isn't installed (the cloakroom[ocr] extra)"), False
    from . import ocr as engine
    try:
        text, ms = engine.image_text(base64.b64decode(data, validate=False), ocr.timeout)
    except Exception as e:  # undecodable, not a picture, OCR failed or too slow
        ocr.error = str(e) or type(e).__name__
        return MCP_IMAGE.format(why="it couldn't be read by local OCR"), False
    ocr.ms += ms
    text = unicodedata.normalize("NFKC", text).strip()
    if len(re.sub(r"\s", "", text)) < 2:
        return MCP_IMAGE.format(why="local OCR found no text in it"), False
    ocr.pages += 1
    return OCR_NOTE.format(what="image") + text, True


def convert_mcp(response, allow_images: bool = False, ocr: Ocr | None = None):
    """Replace every image block in an MCP tool result. Returns (response, counts) with counts
    {"ocr": n, "held": n, "allowed": n}; the response is unchanged when there is no image in it."""
    counts = {"ocr": 0, "held": 0, "allowed": 0}

    def walk(v):
        if isinstance(v, list):
            return [walk(x) for x in v]
        if not isinstance(v, dict):
            return v
        is_image, data = _mcp_image(v)
        if not is_image:
            return {k: walk(x) for k, x in v.items()}
        if allow_images:
            counts["allowed"] += 1
            return v
        if data is None:  # an image block we can't decode: fail closed
            counts["held"] += 1
            return {"type": "text", "text": MCP_IMAGE.format(why="it isn't in a form Cloakroom can read")}
        text, read = _mcp_ocr(data, ocr)
        counts["ocr" if read else "held"] += 1
        return {"type": "text", "text": text}

    if isinstance(response, str) and response.lstrip()[:1] in ("[", "{") and '"image' in response:
        import json
        try:
            parsed = json.loads(response)
        except ValueError:
            return response, counts
        new = walk(parsed)
        return (json.dumps(new) if new != parsed else response), counts
    return walk(response), counts
