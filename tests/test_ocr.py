"""OCR of scanned PDFs and pictures (ocr.py, the `ocr` extra). Fixtures are drawn here with PIL; values are fake.

Without the extra these tests skip, except the ones that check the held-back behaviour stays as it was."""
import base64
import io
import json
import sys

import pytest

from cloakroom import engine

pytest.importorskip("pypdf")

LINES = ["Harbor Point Bank - Monthly Statement", "Account holder: Margaret Ellison",
         "Account number: 4417 2290 1185 3306", "Email: m.ellison@fernhill.co", "Closing balance: 2,418.55"]


def _font(size=32):
    from PIL import ImageFont
    for f in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def picture(lines=LINES, fmt="PNG") -> bytes:
    """A white page with black text, like a phone photo or a scan of a statement."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1240, 120 + 80 * len(lines)), "white")
    d = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        d.text((60, 60 + i * 80), line, fill="black", font=_font())
    buf = io.BytesIO()
    im.save(buf, fmt, **({"resolution": 150} if fmt == "PDF" else {}))
    return buf.getvalue()


def scanned_pdf(pages=1) -> bytes:
    """An image-only PDF (no text layer), as a scanner makes."""
    from PIL import Image
    ims = [Image.open(io.BytesIO(picture())).convert("RGB") for _ in range(pages)]
    buf = io.BytesIO()
    ims[0].save(buf, "PDF", resolution=150, save_all=True, append_images=ims[1:])
    return buf.getvalue()


def run_post(data, monkeypatch, capsys):
    from cloakroom import hook
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    hook.main("post")
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def pdf_read(path, pdf: bytes):
    path.write_bytes(pdf)
    return {"session_id": "o", "tool_name": "Read", "tool_input": {"file_path": str(path)},
            "tool_response": {"type": "pdf", "file": {"filePath": str(path),
                                                      "base64": base64.b64encode(pdf).decode(), "originalSize": len(pdf)}}}


def image_read(path, data: bytes, mime="image/png"):
    return {"session_id": "o", "tool_name": "Read", "tool_input": {"file_path": str(path)},
            "tool_response": {"type": "image", "file": {"base64": base64.b64encode(data).decode(), "type": mime,
                                                        "originalSize": len(data)}}}


def config(**kw):
    from cloakroom import config as c
    c.private_dir()
    (c.HOME / "config.json").write_text(json.dumps(kw))


def hook_log():
    from cloakroom import config as c
    return (c.private_dir() / "hook.log").read_text()


def assert_masked(text):
    assert "Harbor Point Bank" in text and "local OCR" in text
    assert "fernhill" not in text and "PII_EMAIL_1" in text
    assert "4417" not in text and "3306" not in text and "PII_ACCOUNT_1" in text


# --- the digit fallback: works without the extra -------------------------------------------------------------------

@pytest.mark.parametrize("number", ["4417 2290 1185 3306", "4417 229011 85330", "4417229011853306"])
def test_ocr_text_masks_account_numbers_in_any_grouping(number):
    text = f"Account holder: Margaret Ellison\nAccount number: {number}\nSort code: 20-45-17\nBalance: 2,418.55"
    found, _ = engine.detect(text, "rules", ocr=True)
    values = [text[f.start:f.end] for f in found]
    assert number in values
    assert not any("20-45-17" in v or "2,418" in v for v in values)  # short numbers and amounts stay


def test_digit_fallback_only_for_ocr_text():
    text = "Account number: 4417 2290 1185 3306"
    assert not engine.detect(text, "rules")[0] and engine.detect(text, "rules", ocr=True)[0]


# --- held back as before when OCR can't help -----------------------------------------------------------------------

def test_without_ocr_scans_and_pictures_are_held_back(monkeypatch, capsys, tmp_path):
    pytest.importorskip("PIL")
    from cloakroom import ocr
    monkeypatch.setattr(ocr, "available", lambda: False)  # the 0.3.0 install: no extra
    res = run_post(pdf_read(tmp_path / "scan.pdf", scanned_pdf()), monkeypatch, capsys)
    assert "held back this PDF" in res["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]
    res = run_post(image_read(tmp_path / "s.png", picture()), monkeypatch, capsys)
    assert "held back this image" in res["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]


# --- with the extra -------------------------------------------------------------------------------------------------

needs_ocr = pytest.mark.skipif(not __import__("cloakroom.ocr").ocr.available(), reason="ocr extra not installed")


@needs_ocr
def test_scanned_pdf_becomes_masked_text(monkeypatch, capsys, tmp_path):
    out = run_post(pdf_read(tmp_path / "scan.pdf", scanned_pdf()), monkeypatch, capsys)
    res = out["hookSpecificOutput"]["updatedToolOutput"]
    assert res["type"] == "text" and res["file"]["filePath"].endswith("scan.pdf")
    assert_masked(res["file"]["content"])
    assert "base64" not in json.dumps(res) and "OCR text" in out["systemMessage"]
    assert "post Read masked ocr pages=1 ms=" in hook_log()


@needs_ocr
@pytest.mark.parametrize("fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_photo_becomes_masked_text(monkeypatch, capsys, tmp_path, fmt, mime):
    out = run_post(image_read(tmp_path / "s.img", picture(fmt=fmt), mime), monkeypatch, capsys)
    res = out["hookSpecificOutput"]["updatedToolOutput"]
    assert res["type"] == "text"
    assert_masked(res["file"]["content"])
    assert "iVBOR" not in json.dumps(res) and "/9j/" not in json.dumps(res)


@needs_ocr
def test_mixed_pdf_keeps_text_layer_and_reads_the_scan(monkeypatch, capsys, tmp_path):
    from pypdf import PdfReader, PdfWriter
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from test_media import make_pdf
    w = PdfWriter()
    for src in (make_pdf([["Cover letter for the enclosed statement, page one"]]), scanned_pdf()):
        w.add_page(PdfReader(io.BytesIO(src)).pages[0])
    buf = io.BytesIO()
    w.write(buf)
    text = run_post(pdf_read(tmp_path / "mix.pdf", buf.getvalue()), monkeypatch, capsys)[
        "hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]
    assert "--- page 1 ---\nCover letter" in text and "--- page 2 ---" in text
    assert_masked(text)


@needs_ocr
def test_max_pages_leaves_the_rest_held_back(monkeypatch, capsys, tmp_path):
    config(ocr_max_pages=1)
    text = run_post(pdf_read(tmp_path / "scan.pdf", scanned_pdf(pages=2)), monkeypatch, capsys)[
        "hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]
    assert "--- page 1 ---" in text and text.split("--- page 2 ---")[1].strip() == "[no text layer on this page: held back]"


@needs_ocr
def test_ocr_timeout_holds_back(monkeypatch, capsys, tmp_path):
    import time

    from cloakroom import ocr
    monkeypatch.setattr(ocr, "_engine", lambda: (time.sleep(2), None)[1])
    config(ocr_timeout=0.2)
    res = run_post(pdf_read(tmp_path / "scan.pdf", scanned_pdf()), monkeypatch, capsys)
    assert "held back this PDF" in res["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]
    assert "ocr failed (OCR took longer than 0.2 s)" in hook_log()
    res = run_post(image_read(tmp_path / "s.png", picture()), monkeypatch, capsys)
    assert "held back this image" in res["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]


@needs_ocr
def test_ocr_failure_and_off_switch_hold_back(monkeypatch, capsys, tmp_path):
    res = run_post(image_read(tmp_path / "s.png", b"not a picture"), monkeypatch, capsys)  # unreadable image
    assert "held back this image" in res["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]
    config(ocr=False)
    res = run_post(pdf_read(tmp_path / "scan.pdf", scanned_pdf()), monkeypatch, capsys)
    assert "held back this PDF" in res["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]


@needs_ocr
def test_blank_picture_is_held_back(monkeypatch, capsys, tmp_path):
    res = run_post(image_read(tmp_path / "b.png", picture(lines=[])), monkeypatch, capsys)
    assert "held back this image" in res["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]


@needs_ocr
def test_encrypted_scan_is_not_rendered(monkeypatch, capsys, tmp_path):
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(scanned_pdf())))
    w.encrypt(user_password="", owner_password="owner-secret")
    buf = io.BytesIO()
    w.write(buf)
    res = run_post(pdf_read(tmp_path / "enc.pdf", buf.getvalue()), monkeypatch, capsys)
    assert "held back this PDF" in res["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]
