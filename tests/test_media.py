"""PDF, image and notebook Read results (media.py): fixtures are built here, values are fake."""
import base64
import io
import json
import sys

import pytest

pytest.importorskip("pypdf")


def make_pdf(pages: list[list[str]]) -> bytes:
    """A minimal PDF, one text line per entry, Helvetica. An empty list makes a page with no text layer."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        ops = "".join(f"BT /F1 12 Tf 72 {720 - 18 * i} Td ({line}) Tj ET\n" for i, line in enumerate(lines))
        objs.append(f"<< /Length {len(ops)} >>\nstream\n{ops}endstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {len(objs)} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    out.write("".join(f"{off:010d} 00000 n \n" for off in offsets).encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


STATEMENT = ["Brightwater Credit Union statement", "Email: jo.smith@fernhill.co",
             "API key: ghp_" + "a1B2" * 9, "Period: September 2026"]


def run_post(data, monkeypatch, capsys):
    from cloakroom import hook
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    hook.main("post")
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def pdf_read(path, pdf: bytes):
    return {"session_id": "m", "tool_name": "Read", "tool_input": {"file_path": str(path)},
            "tool_response": {"type": "pdf", "file": {"filePath": str(path),
                                                      "base64": base64.b64encode(pdf).decode(), "originalSize": len(pdf)}}}


def test_pdf_becomes_masked_text(monkeypatch, capsys, tmp_path):
    out = run_post(pdf_read(tmp_path / "s.pdf", make_pdf([STATEMENT])), monkeypatch, capsys)
    res = out["hookSpecificOutput"]["updatedToolOutput"]
    assert res["type"] == "text" and res["file"]["filePath"].endswith("s.pdf")  # the shape Claude Code accepts
    text = res["file"]["content"]
    assert "Brightwater Credit Union" in text and "PII_EMAIL_1" in text
    assert "fernhill" not in text and "a1B2a1B2" not in text and "base64" not in json.dumps(res)


def test_pdf_ligatures_are_normalised(monkeypatch, capsys, tmp_path):
    """PDF text often has "ﬁ" for "fi", which hides an email from the detectors (seen on a Chrome-printed PDF)."""
    import pypdf
    monkeypatch.setattr(pypdf.PageObject, "extract_text", lambda self, *a, **k: "Email: jo.smith@\ufb01rnhill.co")
    out = run_post(pdf_read(tmp_path / "s.pdf", make_pdf([STATEMENT])), monkeypatch, capsys)
    text = out["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]
    assert "PII_EMAIL_1" in text and "rnhill" not in text


def test_scanned_pdf_is_held_back(monkeypatch, capsys, tmp_path):
    out = run_post(pdf_read(tmp_path / "scan.pdf", make_pdf([[]])), monkeypatch, capsys)
    res = out["hookSpecificOutput"]["updatedToolOutput"]
    assert res["type"] == "text" and "held back this PDF" in res["file"]["content"]
    assert "no text layer" in res["file"]["content"] and "pdftotext" in res["file"]["content"]


def test_pdf_page_without_text_is_marked_and_rest_masked(monkeypatch, capsys, tmp_path):
    out = run_post(pdf_read(tmp_path / "mix.pdf", make_pdf([STATEMENT, []])), monkeypatch, capsys)
    text = out["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]
    assert "--- page 2 ---\n[no text layer on this page: held back]" in text and "fernhill" not in text


def test_broken_pdf_is_held_back(monkeypatch, capsys, tmp_path):
    out = run_post(pdf_read(tmp_path / "bad.pdf", b"%PDF-1.4 not really"), monkeypatch, capsys)
    res = out["hookSpecificOutput"]["updatedToolOutput"]
    assert res["type"] == "text" and "couldn't be read" in res["file"]["content"]


def test_pdf_without_pypdf_is_held_back(monkeypatch, capsys, tmp_path):
    monkeypatch.setitem(sys.modules, "pypdf", None)  # import fails
    out = run_post(pdf_read(tmp_path / "s.pdf", make_pdf([STATEMENT])), monkeypatch, capsys)
    text = out["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]
    assert "pypdf" in text and "fernhill" not in text


def test_pdf_pages_read_uses_those_pages(monkeypatch, capsys, tmp_path):
    p = tmp_path / "two.pdf"
    p.write_bytes(make_pdf([["Cover page of the quarterly report"], STATEMENT]))
    data = {"session_id": "m", "tool_name": "Read", "tool_input": {"file_path": str(p), "pages": "2"},
            "tool_response": {"type": "parts", "file": {"filePath": str(p), "originalSize": 1, "outputDir": "/x",
                                                        "count": 1}, "firstPage": 2,
                              "pages": [{"base64": "/9j/AAAA", "mediaType": "image/jpeg"}]}}
    res = run_post(data, monkeypatch, capsys)["hookSpecificOutput"]["updatedToolOutput"]
    text = res["file"]["content"]
    assert res["type"] == "text" and "Brightwater" in text and "Cover page" not in text and "fernhill" not in text
    assert "/9j/" not in json.dumps(res)


def image_read(path):
    return {"session_id": "m", "tool_name": "Read", "tool_input": {"file_path": str(path)},
            "tool_response": {"type": "image", "file": {"base64": "iVBORw0KGgoAAAANSUhEUg", "type": "image/png",
                                                        "originalSize": 73}}}


def test_image_is_held_back(monkeypatch, capsys, tmp_path):
    out = run_post(image_read(tmp_path / "shot.png"), monkeypatch, capsys)
    res = out["hookSpecificOutput"]["updatedToolOutput"]
    assert res["type"] == "text" and res["file"]["filePath"].endswith("shot.png")
    assert "held back this image" in res["file"]["content"] and "iVBOR" not in json.dumps(res)


def test_image_passes_with_warning_when_allowed(monkeypatch, capsys, tmp_path):
    from cloakroom import config
    config.private_dir()
    (config.HOME / "config.json").write_text(json.dumps({"allow_images": True}))
    out = run_post(image_read(tmp_path / "shot.png"), monkeypatch, capsys)
    assert "hookSpecificOutput" not in out and "unchecked" in out["systemMessage"]


def test_notebook_figures_dropped_text_masked(monkeypatch, capsys):
    data = {"session_id": "m", "tool_name": "Read", "tool_input": {"file_path": "/p/nb.ipynb"},
            "tool_response": {"type": "notebook", "file": {"filePath": "/p/nb.ipynb", "cells": [
                {"cellType": "code", "source": "print(email)", "cell_id": "cell-0", "language": "python",
                 "outputs": [{"output_type": "stream", "text": "jo.smith@fernhill.co\n"},
                             {"output_type": "display_data", "text": "<Figure>",
                              "image": {"image_data": "iVBORw0KGgo", "media_type": "image/png"}}]}]}}}
    res = run_post(data, monkeypatch, capsys)["hookSpecificOutput"]["updatedToolOutput"]
    outs = res["file"]["cells"][0]["outputs"]
    assert res["type"] == "notebook" and "image" not in outs[1] and "figure held back" in outs[1]["text"]
    assert "fernhill" not in json.dumps(res)


def test_unknown_read_type_is_held_back(monkeypatch, capsys):
    data = {"session_id": "m", "tool_name": "Read", "tool_input": {"file_path": "/p/x.bin"},
            "tool_response": {"type": "audio", "file": {"filePath": "/p/x.bin", "base64": "AAAA"}}}
    res = run_post(data, monkeypatch, capsys)["hookSpecificOutput"]["updatedToolOutput"]
    assert res["type"] == "text" and "audio" in res["file"]["content"]


def test_fail_closed_on_pdf_uses_text_shape(monkeypatch, capsys, tmp_path):
    """A crash while checking a PDF must not keep the pdf shape (Claude Code would ignore it and send the PDF)."""
    from cloakroom import hookentry, media
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(media, "convert", boom)
    data = pdf_read(tmp_path / "s.pdf", make_pdf([STATEMENT]))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(data).encode())))
    hookentry.run("post")
    res = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["updatedToolOutput"]
    assert res["type"] == "text" and "base64" not in json.dumps(res) and "stopped" in res["file"]["content"]


def test_text_read_without_type_is_unchanged_path(monkeypatch, capsys):
    from cloakroom import media
    r = {"file": {"content": "hello"}}
    assert media.convert(r) == (r, "")
