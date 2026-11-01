"""Passport numbers after a Passport label are masked by their own rule (the keyed-secret rule caught them only
because "pass" matched inside "Passport"). All values are made up."""
import pytest

from cloakroom import engine


def _masked(text, value):
    return value not in engine.mask(text, mode="rules").text


@pytest.mark.parametrize("text,value", [
    ("Passport: 202833903", "202833903"),
    ("Passport: 658755519", "658755519"),
    ("Passport: X40250068", "X40250068"),
    ("Passport No: 123456789", "123456789"),
    ("passport number: AB1234567", "AB1234567"),
    ("Passport #: C01X00T47", "C01X00T47"),
])
def test_passport_number_is_masked(text, value):
    assert _masked(text, value)


@pytest.mark.parametrize("text,kept", [
    ("passport photo: yes", ["passport", "yes"]),
    ("Passport: valid until 2030", ["Passport", "valid"]),
    ("passport_expiry: 2031-04-01", ["passport_expiry"]),
    ("Passport: <passport number>", ["<passport number>"]),
    ("Passport: approved", ["approved"]),
])
def test_passport_look_alikes_stay(text, kept):
    out = engine.mask(text, mode="rules").text
    assert all(k in out for k in kept)
