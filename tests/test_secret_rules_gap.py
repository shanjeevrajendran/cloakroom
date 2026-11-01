"""Vendor keys that used to pass unmasked when they stood alone (no `token =` label in front). All values are made up
and built at run time, so no key-shaped literal sits in the repo."""
import random
import string

import pytest

from cloakroom import engine

_rng = random.Random(7)
ALNUM = string.ascii_letters + string.digits


def _r(n, alphabet=ALNUM):
    return "".join(_rng.choice(alphabet) for _ in range(n))


def _masked(text, value):
    return value not in engine.mask(text, mode="rules").text


_pgp_body = "\n".join(_r(64, ALNUM + "+/") for _ in range(3))
KEYS = {
    "pgp-private-key": "-----BEGIN " + "PGP PRIVATE KEY BLOCK-----\n\n" + _pgp_body + "\n-----END PGP PRIVATE KEY BLOCK-----",
    "gitlab": "glpat-" + _r(20, ALNUM + "_-"),
    "npm": "npm_" + _r(36),
    "pypi": "pypi-AgEIcHlwaS5vcmc" + _r(60, ALNUM + "_-"),
    "digitalocean": "dop_v1_" + _r(64, "0123456789abcdef"),
    "xai": "xai-" + _r(80),
    "groq": "gsk_" + _r(52),
    "slack-webhook": "https://hooks.slack.com/services/T" + _r(10) + "/B" + _r(10) + "/" + _r(24),
    "discord-webhook": "https://discord.com/api/webhooks/" + _r(18, string.digits) + "/" + _r(68, ALNUM + "_-"),
    "telegram-bot": _r(9, string.digits) + ":AA" + _r(33, ALNUM + "_-"),
}


@pytest.mark.parametrize("name", KEYS)
def test_bare_vendor_key_is_masked(name):
    value = KEYS[name]
    text = f"here it is: {value} (from the logs)"
    if name == "pgp-private-key":
        assert _pgp_body not in engine.mask(text, mode="rules").text
    else:
        assert _masked(text, value)


@pytest.mark.parametrize("text,value", [
    ("run npm_install first", "npm_install"),
    ("see https://hooks.slack.com/ for setup", "https://hooks.slack.com/"),
    ("pip install pypi-simple", "pypi-simple"),
    ("the glpat- prefix marks a GitLab token", "glpat-"),
    ("meeting at 12:AA room", "12:AA"),
])
def test_near_misses_stay_unmasked(text, value):
    assert not _masked(text, value)
