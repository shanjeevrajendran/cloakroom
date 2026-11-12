"""The model reads text a window at a time, and a window never holds more than the model can read. Before 1.5 a
window was 6000 characters whatever it held; dense text (a log, a CSV of numbers, a hex dump) has more than the
model's 4096 tokens in that many characters, and both backends dropped the rest without a word: a name in the last
part of each window went through unmasked."""
import os
import random
from collections import namedtuple
from pathlib import Path

import pytest

from cloakroom import engine

Span = namedtuple("Span", "start end label score")
NAME = "Ingrid Thies"


class DigitModel:
    """A stand-in with a tokenizer like the real one where it matters: a digit is a token, a word is a token. It
    reads at most `limit` tokens of what it is given (the real model: 4096) and finds NAME in what it read."""

    def __init__(self, limit=4096):
        self.limit, self.seen = limit, []

    def _ends(self, text):
        import re
        return [m.end() for m in re.finditer(r"\d|[^\W\d]+|[^\w\s]", text)]

    def fit(self, text, limit):
        ends = self._ends(text)
        return len(text) if len(ends) <= limit else ends[limit - 1]

    def predict(self, text):
        ends = self._ends(text)
        read = text if len(ends) <= self.limit else text[:ends[self.limit - 1]]
        self.seen.append(text)
        spans, i = [], read.find(NAME)
        while i >= 0:
            spans.append(Span(i, i + len(NAME), "private_person", 0.99))
            i = read.find(NAME, i + 1)
        return spans, None


@pytest.fixture
def model(monkeypatch):
    m = DigitModel()
    monkeypatch.setattr(engine, "_MODEL", m)
    return m


def _numbers(chars, seed=3):
    rng, lines, size = random.Random(seed), [], 0
    while size < chars:
        lines.append(",".join(str(rng.randint(0, 99999)) for _ in range(12)))
        size += len(lines[-1]) + 1
    return "\n".join(lines)[:chars]


def _found(text):
    return [text[f.start:f.end] for f in engine.model_findings(text)]


def test_a_name_at_the_end_of_dense_text_is_found(model):
    line = f"contact,{NAME},note"
    body = _numbers(engine.CHUNK_CHARS - len(line) - 1)
    text = body + "\n" + line  # 6000 characters, about 6000 tokens: the model reads 4096 of them at a time
    assert _found(text) == [NAME]
    assert all(len(model._ends(seen)) <= engine.WINDOW_TOKENS for seen in model.seen)


@pytest.mark.parametrize("at", [0, 2500, 4100, 5900, 6000, 9000, 13500])
def test_a_name_anywhere_in_long_dense_text_is_found(model, at):
    body = _numbers(14000)
    at = body.rfind("\n", 0, at) + 1  # on its own line
    text = body[:at] + f"contact,{NAME},note\n" + body[at:]
    assert _found(text) == [NAME]


def test_windows_cover_every_character_and_stop_at_line_breaks():
    text = _numbers(40000)
    m = DigitModel()
    windows = engine._windows(text, lambda chunk: m.fit(chunk, engine.WINDOW_TOKENS))
    assert windows[0][0] == 0 and windows[-1][1] == len(text)
    for (a, b), (c, d) in zip(windows, windows[1:]):
        assert a < c <= b < d  # no gap, each one moves on
        assert text[b - 1] == "\n"  # ends after a whole line
        assert c == 0 or text[c - 1] == "\n"  # and the next starts at a line
        assert b - c <= engine.OVERLAP_CHARS
    assert all(len(m._ends(text[a:b])) <= engine.WINDOW_TOKENS for a, b in windows)


def test_text_without_line_breaks_is_cut_at_a_separator_with_an_overlap():
    text = " ".join(f"word{i}" for i in range(4000))  # one long line
    windows = engine._windows(text, len)
    assert windows[0][0] == 0 and windows[-1][1] == len(text) and len(windows) > 1
    for (a, b), (c, d) in zip(windows, windows[1:]):
        assert text[b - 1] == " " and text[c - 1] == " " and a < c < b < d


def test_text_with_no_place_to_cut_still_moves_on():
    text = "x" * 20000
    windows = engine._windows(text, len)
    assert windows[0][0] == 0 and windows[-1][1] == len(text)
    assert all(a < c <= b for (a, b), (c, _) in zip(windows, windows[1:]))


def test_a_name_across_a_cut_is_whole_in_one_window(model):
    line = "The service restarted after the deploy and the queue drained without any errors today.\n"
    for shift in range(0, len(NAME) + 1):
        head = (line * 80)[:engine.CHUNK_CHARS - shift - len("Signed by ")] + "Signed by "
        text = head + NAME + " for the tenant.\n" + line * 5
        assert text[engine.CHUNK_CHARS - shift:][:len(NAME)] == NAME
        assert _found(text) == [NAME], shift


def test_short_text_is_one_window_as_before(model):
    text = f"Signed by {NAME}.\n"
    assert _found(text) == [NAME] and model.seen == [text]


def test_a_finding_in_the_overlap_is_reported_once(model):
    text = _numbers(5000) + f"\ncontact,{NAME},note\n" + _numbers(5000, seed=4)
    assert _found(text).count(NAME) == 1


def _onnx_dir() -> Path:
    from cloakroom import onnx_backend
    return Path(os.environ.get("CLOAKROOM_ONNX_DIR") or onnx_backend.model_dir())


@pytest.fixture
def onnx(monkeypatch):
    pytest.importorskip("onnxruntime")
    from cloakroom import onnx_backend
    if not (_onnx_dir() / onnx_backend.FP32).exists():
        pytest.skip("ONNX export not found: run scripts/export_onnx.py")
    monkeypatch.setenv("CLOAKROOM_BACKEND", "onnx")
    monkeypatch.setenv("CLOAKROOM_ONNX_DIR", str(_onnx_dir()))
    monkeypatch.setattr(engine, "_MODEL", None)
    yield onnx_backend
    monkeypatch.setattr(engine, "_MODEL", None)


def test_the_real_model_reads_the_end_of_a_log(onnx):
    rng = random.Random(7)
    rows = [f'203.0.113.{i % 250} - - [11/Nov/2026:10:{i % 60:02d}:{i % 60:02d} +0000] "GET /api/v1/items/'
            f'{rng.randint(1, 99999)} HTTP/1.1" 200 {rng.randint(100, 9999)}' for i in range(80)]
    line = "Emergency contact is her mother Ingrid Thies, who lives nearby."
    text = "\n".join(rows)[:engine.CHUNK_CHARS - len(line) - 1] + "\n" + line
    out = engine.mask(text, mode="full").text
    assert NAME not in out


def test_the_real_model_refuses_text_it_would_cut(onnx):
    m = onnx.OnnxModel(_onnx_dir() / onnx.FP32)
    with pytest.raises(ValueError):
        m.predict("7 " * 5000)
    assert m.fit("7 " * 5000, 100) < 400
    assert m.fit("a short line", 100) == len("a short line")
