"""Samsung CredData (credentials in public repositories, obfuscated) scored line by line, without the file path,
the way a tool sees a prompt or a tool output.

    python bench/credata.py CREDDATA_DIR OUT.json adapter [adapter ...]

Rows: single-line labels with a value position (GroundTruth T = credential, F = a candidate that is not one).
A tool "hits" a row when the labelled value no longer appears in its output (masked, withheld or blocked).
T + hit = true positive, T + miss = false negative, F + hit = false positive. Only counts are written, no values.
"""
import collections
import csv
import glob
import hashlib
import importlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "adapters"))
root, out_path, names = sys.argv[1], sys.argv[2], sys.argv[3:]

rows = []
for f in sorted(glob.glob(os.path.join(root, "meta", "*.csv"))):
    for r in csv.DictReader(open(f, encoding="utf-8")):
        if r["GroundTruth"] not in ("T", "F") or r["LineStart"] != r["LineEnd"] or r["ValueStart"] in ("", "-1"):
            continue
        # SPLIT=tune|holdout: half the repositories each (by a stable hash of the repo id), so a rule can be tuned
        # on one half and scored once on the other
        split = os.environ.get("SPLIT")
        if split and (int(hashlib.sha256(r["RepoName"].encode()).hexdigest(), 16) % 2 == 0) != (split == "tune"):
            continue
        if r["RepoName"] in os.environ.get("EXCLUDE_REPOS", "").split(","):  # e.g. a repo of crypto test vectors
            continue
        rows.append(r)

lines_cache = {}


def line_of(r):
    path = os.path.join(root, r["FilePath"])
    if path not in lines_cache:
        try:
            lines_cache[path] = open(path, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            lines_cache[path] = None
    lines = lines_cache[path]
    n = int(r["LineStart"]) - 1
    return lines[n] if lines and 0 <= n < len(lines) else None


items = []
for r in rows:
    line = line_of(r)
    if line is None:
        continue
    a = int(r["ValueStart"])
    b = int(r["ValueEnd"]) if r["ValueEnd"] not in ("", "-1") else len(line)
    value = line[a:b]
    if len(value) < 4:
        continue
    items.append((r["GroundTruth"], r["Category"], line, value))
print(f"{len(items)} rows ({sum(t == 'T' for t, *_ in items)} T, {sum(t == 'F' for t, *_ in items)} F)", flush=True)

results = []
for name in names:
    ad = importlib.import_module(name)
    seen, c, cat = {}, collections.Counter(), collections.defaultdict(collections.Counter)
    t0 = time.perf_counter()
    for gt, category, line, value in items:
        if line not in seen:
            out, blocked = ad.mask(line)
            seen[line] = (out, blocked)
        out, blocked = seen[line]
        hit = blocked or value not in out
        key = {("T", True): "tp", ("T", False): "fn", ("F", True): "fp", ("F", False): "tn"}[(gt, hit)]
        c[key] += 1
        cat[category][key] += 1
    p = c["tp"] / max(1, c["tp"] + c["fp"])
    rc = c["tp"] / max(1, c["tp"] + c["fn"])
    res = {"adapter": ad.NAME, **c, "precision": round(p, 3), "recall": round(rc, 3),
           "f1": round(2 * p * rc / max(1e-9, p + rc), 3), "unique_lines": len(seen),
           "seconds": round(time.perf_counter() - t0), "by_category": {k: dict(v) for k, v in cat.items()}}
    results.append(res)
    print({k: res[k] for k in ("adapter", "tp", "fn", "fp", "tn", "precision", "recall", "f1", "seconds")}, flush=True)
    json.dump(results, open(out_path, "w"), indent=2)
