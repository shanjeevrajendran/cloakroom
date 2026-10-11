"""Mcpgate-de/maisecrets detect.scan (gitleaks + presidio + detect-secrets rules), default regions."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "competitors", "maisecrets"))
from maisecrets import detect  # noqa: E402

NAME = "maisecrets"


def mask(text):
    for i, m in enumerate(sorted(detect.scan(text), key=lambda m: -m.start)):
        text = text[:m.start] + f"[{m.type}_{i}]" + text[m.end:]
    return text, False
