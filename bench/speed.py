"""How long Cloakroom's steps take on this machine, as one table. Run it on two versions to compare them:

    python bench/speed.py                 rules only
    python bench/speed.py --model         also the model (needs cloakroom[model]; CLOAKROOM_BACKEND picks one)

It uses a data folder and a vault key of its own, made for the run and deleted after it. Texts are generated here
(no real data) and the numbers are the best of three runs.
"""
import base64
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
HOME = tempfile.mkdtemp(prefix="cloakroom-speed-")
os.environ.update(CLOAKROOM_HOME=HOME, CLOAKROOM_VAULT_KEY=base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("CLOAKROOM_MODE", "rules")

import cloakroom  # noqa: E402
from cloakroom import config, engine  # noqa: E402
from cloakroom.vault import Vault  # noqa: E402

RNG = random.Random(7)
NAME = "Ingrid Thies"


def csv(rows, values):
    return "\n".join(f"row {i}, user{i % values}@quillmere.net, 415-820-{4000 + i % min(values, 6000):04d}, note text "
                     "here" for i in range(rows))


def prose(chars, run=0):
    lines, size, i = [], 0, 0
    while size < chars:
        lines.append(f"Line {i}.{run}: the service restarted after the deploy and the queue drained without any errors.")
        size, i = size + len(lines[-1]) + 1, i + 1
    return "\n".join(lines)[:chars]


def log(chars, run=0):
    lines, size, i = [], 0, 0
    while size < chars:
        lines.append(f'203.0.{run}.{i % 250} - - [11/Nov/2026:10:{i % 60:02d}:{i % 60:02d} +0000] "GET /api/v1/items/'
                     f'{RNG.randint(1, 99999)} HTTP/1.1" 200 {RNG.randint(100, 9999)}')
        size, i = size + len(lines[-1]) + 1, i + 1
    return "\n".join(lines)[:chars]


def best(fn, runs=3):
    out = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    return min(out)


def row(what, ms, note=""):
    print(f"| {what} | {ms:,.0f} ms | {note} |", flush=True)


def hook_call():
    data = ('{"session_id":"speed","cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"cat x"},"tool_response":'
            '{"stdout":"hello, contact priya@quillmere.net or 415-820-4471","stderr":""}}')
    env = dict(os.environ, PYTHONPATH=os.path.join(HERE, "..", "src"))
    subprocess.run([sys.executable, "-m", "cloakroom", "hook", "post"], input=data, capture_output=True, text=True,
                   env=env, check=True)


def main():
    print(f"Cloakroom {cloakroom.__version__}, Python {sys.version.split()[0]}, {sys.platform}\n")
    print("| step | time | note |\n|---|---|---|")
    row("one hook call, start to answer (rules)", best(hook_call), "a new process per tool call")
    for rows, values in ((500, 300), (2000, 300), (8000, 300), (8000, 8000)):
        text = csv(rows, values)
        found = len(engine.detect(text, "rules")[0])
        row(f"rules over a {len(text) // 1000} KB CSV", best(lambda: engine.detect(text, "rules")),
            f"{found:,} findings, {values:,} different values")
    n = [0]

    def new_values():
        n[0] += 1
        text = "\n".join(f"user{i}@quillmere{n[0]}x{i}.net" for i in range(800))
        engine.mask(text, Vault(config.vault_path(f"speed{n[0]}")), "rules")
    row("mask 800 values never seen before", best(new_values), "the session file is encrypted and written")
    if "--model" in sys.argv:
        engine.mask("warm up", mode="full")
        fresh = [0]

        def unseen(make):  # a text the model has not read: every line of each run is its own
            fresh[0] += 1
            return make(30000, fresh[0])
        row(f"model over 30,000 characters of prose ({engine.BACKEND})", best(lambda: engine.model_findings(unseen(prose))))
        row(f"model over 30,000 characters of log lines ({engine.BACKEND})", best(lambda: engine.model_findings(unseen(log))))
        same = log(30000)
        engine.model_findings(same)
        row("the same 30,000 characters of log lines again", best(lambda: engine.model_findings(same)),
            "a file checked before a command and again as its output")
        line = f"Emergency contact is her mother {NAME}, who lives nearby."
        dense = log(6000 - len(line) - 1) + "\n" + line
        masked = NAME not in engine.mask(dense, mode="full").text
        print(f"\nA name at the end of 6,000 characters of log lines is masked: {'yes' if masked else 'NO'}")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(HOME, ignore_errors=True)
