"""Rerun adapters over the existing holdouts without touching their first-run results files.

    python bench/rerun.py OUTDIR adapter [adapter ...]      sets: holdout_v2 .. holdout_v6 (or SETS="a b")

Writes OUTDIR/<set>_results.json; `RESULTS_DIR=OUTDIR python bench/tiers.py` reads them.
"""
import importlib
import json
import os
import pathlib
import sys

import run_bench

HERE = pathlib.Path(__file__).parent
out, names = pathlib.Path(sys.argv[1]), sys.argv[2:]
out.mkdir(parents=True, exist_ok=True)
for s in os.environ.get("SETS", " ".join(f"holdout_v{n}" for n in range(2, 7))).split():
    corpus = [json.loads(l) for l in (HERE / f"{s}.jsonl").read_text().splitlines()]
    res = [run_bench.score(importlib.import_module(n), corpus) for n in names]
    planted = {x["id"]: x["planted"] for x in corpus}
    for r in res:  # positions in "planted", not the values: the synthetic keys would trip secret scanners
        for leak in r["leaks"]:
            leak["planted_idx"] = [planted[leak["id"]].index(v) for v in leak.pop("values")]
    (out / f"{s}_results.json").write_text(json.dumps(res, indent=2))
    print(s, [(r["adapter"], r["values_leaked"], r["false_alarms"]) for r in res], flush=True)
