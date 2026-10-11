"""One long-lived node process running the bundled TypeScript competitors (see competitors/runner.ts)."""
import json
import os
import subprocess

_RUNNER = os.path.join(os.path.dirname(__file__), "..", "competitors", "sensitive-canary", "src", "lib", "runner.mjs")
_P = None


def run(text):
    global _P
    if _P is None:
        _P = subprocess.Popen(["node", _RUNNER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    _P.stdin.write(json.dumps({"text": text}) + "\n")
    _P.stdin.flush()
    return json.loads(_P.stdout.readline())
