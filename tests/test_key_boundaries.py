"""Model spans that start at the key ("STRIPE_KEY=sk_...") or on the "=" must be trimmed to the value, or restore on
write-back drops the key and corrupts the file. A fake model reproduces the boundaries PII-Tracer gave on 2026-10-13."""
import re
from collections import namedtuple

import pytest

from cloakroom import Vault, engine

Span = namedtuple("Span", "start end label score")
KEY = "sk_" + "live_51Hx9QaBcDeFgHiJkLmNoPqRsTuVwXyZ012345"  # split so secret scanners leave the fake alone
EMAIL = "priya.nair@acme.com"


class FakeModel:
    """Marks each line's whole "KEY<sep>value" as the span, the way the model ran over the key or the "="."""

    def predict(self, text):
        spans = []
        for m in re.finditer(r"(?m)^(\w+)(\s*[=:]\s*)(.+)$", text):
            label = "secret" if m.group(3).startswith("sk_") else "private_email"
            start = m.start() if label == "secret" else m.start(2) + re.search("[=:]", m.group(2)).start()
            spans.append(Span(start, m.end(), label, 0.99))
        return spans, None


@pytest.fixture
def fake_model(monkeypatch):
    monkeypatch.setattr(engine, "_MODEL", FakeModel())


@pytest.mark.parametrize("line, key", [
    (f"STRIPE_KEY={KEY}", "STRIPE_KEY="),
    (f"STRIPE_KEY = {KEY}", "STRIPE_KEY = "),
    (f"SUPPORT_CONTACT={EMAIL}", "SUPPORT_CONTACT="),
    (f"SUPPORT_CONTACT = {EMAIL}", "SUPPORT_CONTACT = "),
    (f"support_contact: {EMAIL}", "support_contact: "),
])
def test_key_and_separator_stay_outside_the_mask(fake_model, line, key):
    v = Vault()
    out = engine.mask(line + "\n", v, mode="full").text
    assert re.fullmatch(re.escape(key) + r"PII_[A-Z]+_\d+\n", out), out
    assert v.restore(out)[0] == line + "\n"


def test_same_value_same_placeholder_with_or_without_spaces(fake_model):
    v = Vault()
    out = engine.mask(f"SUPPORT_CONTACT={EMAIL}\nSUPPORT_CONTACT = {EMAIL}\n", v, mode="full").text
    assert out == "SUPPORT_CONTACT=PII_EMAIL_1\nSUPPORT_CONTACT = PII_EMAIL_1\n"


@pytest.mark.parametrize("span", ["admin:hunter2pass", "https://example.org/x", "dGVzdHZhbHVlMTI=="])
def test_values_that_only_look_keyed_stay_whole(span):
    text = f"x {span} y"
    a = text.index(span)
    assert engine._unkey(text, a, a + len(span)) == (a, a + len(span))
