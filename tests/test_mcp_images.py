"""Images in MCP tool results (media.convert_mcp, hook.on_post): read locally by OCR, never passed on as pixels.
Pictures are drawn here with PIL and the MCP payloads are made up; every value is fake."""
import base64
import io
import json
import sys

import pytest

from cloakroom import media

PIXELS = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()  # an image-typed block OCR can't read
needs_ocr = pytest.mark.skipif(not __import__("cloakroom.ocr").ocr.available(), reason="ocr extra not installed")


def screenshot(lines=("Signed in as m.ellison@fernhill.co", "Order total 418.55")) -> str:
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for f in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "C:/Windows/Fonts/arial.ttf"):
        try:
            font = ImageFont.truetype(f, 32)
            break
        except OSError:
            pass
    im = Image.new("RGB", (1100, 100 + 80 * len(lines)), "white")
    d = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        d.text((50, 50 + 80 * i), line, fill="black", font=font or ImageFont.load_default(size=32))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def mcp_call(response, tool="mcp__browser__screenshot"):
    return {"session_id": "mcp", "tool_name": tool, "tool_input": {}, "tool_response": response}


def run_post(data, monkeypatch, capsys):
    from cloakroom import hook
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    hook.main("post")
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def no_ocr(monkeypatch):
    from cloakroom import ocr
    monkeypatch.setattr(ocr, "available", lambda: False)


def set_config(cfg):
    from cloakroom import config
    config.private_dir()
    (config.HOME / "config.json").write_text(json.dumps(cfg))


# --- shapes, without OCR -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("block", [
    {"type": "image", "data": PIXELS, "mimeType": "image/png"},                                    # MCP
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PIXELS}},     # API
    {"type": "resource", "resource": {"uri": "file:///shot.png", "mimeType": "image/png", "blob": PIXELS}},
])
@pytest.mark.parametrize("wrap", [lambda b: [b], lambda b: {"content": [b]}])
def test_image_block_held_without_ocr(block, wrap, monkeypatch, capsys):
    no_ocr(monkeypatch)
    out = run_post(mcp_call(wrap(block)), monkeypatch, capsys)
    res = out["hookSpecificOutput"]["updatedToolOutput"]
    assert PIXELS not in json.dumps(out)
    assert "OCR isn't installed" in json.dumps(res)
    assert out["hookSpecificOutput"]["updatedMCPToolOutput"] == res
    assert "held back" in out["systemMessage"]


def test_undecodable_image_block_fails_closed(monkeypatch, capsys):
    out = run_post(mcp_call([{"type": "image", "mimeType": "image/png"}]), monkeypatch, capsys)
    assert out["hookSpecificOutput"]["updatedToolOutput"] == [
        {"type": "text", "text": media.MCP_IMAGE.format(why="it isn't in a form Cloakroom can read")}]
    assert "held back" in out["systemMessage"]


def test_unreadable_pixels_held_even_with_ocr(monkeypatch, capsys):
    from cloakroom import ocr
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "image_text", lambda data, timeout: (_ for _ in ()).throw(ocr.OCRError("bad image")))
    out = run_post(mcp_call([{"type": "image", "data": PIXELS, "mimeType": "image/png"}]), monkeypatch, capsys)
    assert PIXELS not in json.dumps(out) and "couldn't be read" in json.dumps(out)


def test_text_blocks_around_an_image_are_still_masked(monkeypatch, capsys):
    no_ocr(monkeypatch)
    resp = [{"type": "text", "text": "Page owner: jo.smith@fernhill.co"},
            {"type": "image", "data": PIXELS, "mimeType": "image/png"}]
    out = run_post(mcp_call(resp), monkeypatch, capsys)
    res = out["hookSpecificOutput"]["updatedToolOutput"]
    assert "jo.smith@fernhill.co" not in json.dumps(res) and res[0]["type"] == "text"
    assert res[1]["type"] == "text" and PIXELS not in json.dumps(res)
    assert "masked" in out["systemMessage"] and "held back" in out["systemMessage"]


def test_json_string_response_is_converted(monkeypatch, capsys):
    no_ocr(monkeypatch)
    resp = json.dumps([{"type": "image", "data": PIXELS, "mimeType": "image/png"}])
    out = run_post(mcp_call(resp), monkeypatch, capsys)
    res = out["hookSpecificOutput"]["updatedToolOutput"]
    assert isinstance(res, str) and PIXELS not in res and json.loads(res)[0]["type"] == "text"


def test_allow_images_passes_image_unchanged(monkeypatch, capsys):
    set_config({"allow_images": True})
    out = run_post(mcp_call([{"type": "image", "data": PIXELS, "mimeType": "image/png"}]), monkeypatch, capsys)
    assert "hookSpecificOutput" not in out and "unchecked" in out["systemMessage"]


def test_resource_link_and_text_only_results_untouched(monkeypatch, capsys):
    resp = [{"type": "resource_link", "uri": "file:///shot.png", "mimeType": "image/png"},
            {"type": "text", "text": "saved the screenshot"}]
    assert media.convert_mcp(resp)[0] == resp
    assert run_post(mcp_call(resp), monkeypatch, capsys) is None


def test_read_tool_not_routed_through_mcp(monkeypatch):
    called = []
    monkeypatch.setattr(media, "convert_mcp", lambda *a, **k: called.append(1))
    from cloakroom import hook
    hook.on_post({"session_id": "x", "tool_name": "Bash", "tool_input": {}, "tool_response": {"stdout": "ok"}})
    assert not called


# --- with the ocr extra ---------------------------------------------------------------------------------------------

@needs_ocr
def test_screenshot_becomes_masked_ocr_text(monkeypatch, capsys):
    shot = screenshot()
    out = run_post(mcp_call({"content": [{"type": "image", "data": shot, "mimeType": "image/png"}]}),
                   monkeypatch, capsys)
    res = out["hookSpecificOutput"]["updatedToolOutput"]
    block = res["content"][0]
    assert block["type"] == "text" and shot not in json.dumps(out)
    assert "local OCR" in block["text"] and "Order total" in block["text"]
    assert "fernhill.co" not in block["text"] and "PII_EMAIL_" in block["text"]
    assert "read by local OCR" in out["systemMessage"]


@needs_ocr
def test_blank_screenshot_is_held(monkeypatch, capsys):
    shot = screenshot(lines=())
    out = run_post(mcp_call([{"type": "image", "data": shot, "mimeType": "image/png"}]), monkeypatch, capsys)
    assert shot not in json.dumps(out) and "found no text" in json.dumps(out)


# --- image-only results, the shape a browser screenshot tool returns (bug found 2026-10-20) -------------------------

CUSTOMER = ("Customer: Henrik Dahl", "Email: henrik.dahl@quillfield.com")  # fake


def full_mode_in_process(monkeypatch):
    """Full mode without the service or model weights: a fake model finds the person, the rules find the email."""
    from cloakroom import Vault, client, config, engine, rules

    def model_findings(text):
        return [rules.Finding(m.start(), m.end(), "PERSON", "model:private_person")
                for m in __import__("re").finditer(r"Henrik\s+Dahl", text)]
    monkeypatch.setattr(engine, "model_findings", model_findings)

    def mask(text, session, ocr=False):
        r = engine.mask(text, Vault(config.vault_path(session)), "full", ocr=ocr)
        return {"text": r.text, "mode": "full", "masked": r.masked, "skipped": r.skipped}
    monkeypatch.setattr(client, "mask", mask)


@needs_ocr
@pytest.mark.parametrize("shape", ["list", "wrapper", "json-string"])
def test_image_only_result_becomes_masked_ocr_text(shape, monkeypatch, capsys):
    full_mode_in_process(monkeypatch)
    shot = screenshot(lines=CUSTOMER)
    block = {"type": "image", "data": shot, "mimeType": "image/png"}
    response = {"list": [block], "wrapper": {"content": [block]}, "json-string": json.dumps([block])}[shape]
    out = run_post(mcp_call(response, tool="mcp__claude-in-chrome__computer"), monkeypatch, capsys)
    h = out["hookSpecificOutput"]
    res = h["updatedToolOutput"]
    assert h["updatedMCPToolOutput"] == res
    blocks = {"list": lambda r: r, "wrapper": lambda r: r["content"], "json-string": json.loads}[shape](res)
    assert [b["type"] for b in blocks] == ["text"]
    text = blocks[0]["text"]
    assert "local OCR" in text and "PII_PERSON_" in text and "PII_EMAIL_" in text
    dump = json.dumps(out)
    assert shot not in dump and "Henrik" not in dump and "Dahl" not in dump and "quillfield" not in dump
    assert "read by local OCR" in out["systemMessage"]


# --- fail closed: an MCP result is never an image block with text in its data field --------------------------------

@pytest.mark.parametrize("shape", ["list", "wrapper", "json-string"])
def test_fail_closed_mcp_image_becomes_text_block(shape, monkeypatch, capsys):
    from cloakroom import client, hook
    no_ocr(monkeypatch)

    def broken(*a, **k):
        raise client.Unavailable("ValueError")
    monkeypatch.setitem(hook.HANDLERS, "post", broken)
    block = {"type": "image", "data": PIXELS, "mimeType": "image/png"}
    response = {"list": [{"type": "text", "text": "Took a screenshot"}, block], "wrapper": {"content": [block]},
                "json-string": json.dumps([block])}[shape]
    out = run_post(mcp_call(response), monkeypatch, capsys)
    h = out["hookSpecificOutput"]
    res = h["updatedToolOutput"]
    assert h["updatedMCPToolOutput"] == res
    blocks = {"list": lambda r: r, "wrapper": lambda r: r["content"], "json-string": json.loads}[shape](res)
    assert all(set(b) == {"type", "text"} and b["type"] == "text" for b in blocks)
    assert "couldn't check this (ValueError)" in blocks[-1]["text"]
    assert PIXELS not in json.dumps(out) and "Took a screenshot" not in json.dumps(out)


def test_env_vault_key_accepts_hex_and_rejects_wrong_length(monkeypatch):
    """A hex key (openssl rand -hex 32) read as base64 is 48 bytes: AES-GCM refused it at the first vault write,
    which made every masked tool result fail closed (2026-10-20)."""
    from cloakroom import crypto
    monkeypatch.setenv("CLOAKROOM_VAULT_KEY", "ab" * 32)
    crypto._KEY = None
    assert crypto.key() == bytes.fromhex("ab" * 32)
    assert crypto.decrypt(crypto.encrypt(b"x")) == b"x"
    monkeypatch.setenv("CLOAKROOM_VAULT_KEY", "c2hvcnQ=")  # "short"
    crypto._KEY = None
    with pytest.raises(crypto.KeyUnavailable, match="256-bit"):
        crypto.key()
    crypto._KEY = None
