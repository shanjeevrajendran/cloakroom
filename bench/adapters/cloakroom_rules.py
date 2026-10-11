import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
import cloakroom  # noqa: E402

NAME = "cloakroom rules"


def mask(text):
    return cloakroom.mask(text, mode="rules").text, False
