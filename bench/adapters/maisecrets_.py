"""Mcpgate-de/maisecrets detect.scan (betterleaks + presidio + detect-secrets rules), default regions.

Since 0.6.17 the hook scans tool results and files with channel="tool" (Mcpgate-de/maisecrets#17); the bench
measures that channel. Before 0.6.17 (and in results dated before 2026-10-14) it was the prompt channel."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "competitors", "maisecrets"))
from maisecrets import detect  # noqa: E402

NAME = "maisecrets"


def mask(text):
    for i, m in enumerate(sorted(detect.scan(text, channel="tool"), key=lambda m: -m.start)):
        text = text[:m.start] + f"[{m.type}_{i}]" + text[m.end:]
    return text, False
