"""Run every adapter over the corpus and score it.

Adapter = module in bench/adapters with NAME and mask(text) -> (output_text, blocked: bool).
Scores (planted items): items_leaked, values_leaked / values_total, blocked. Negatives: false alarms
(output changed or blocked). Blocking counts as protected but costs usability, so it is reported apart.

    python bench/run_bench.py [adapter ...]
"""
import importlib
import json
import pathlib
import sys
import os
import time

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE / "adapters"))


def score(adapter, corpus):
    r = {"adapter": adapter.NAME, "items": 0, "items_leaked": 0, "values_total": 0, "values_leaked": 0,
         "blocked": 0, "negatives": 0, "false_alarms": 0, "ms": 0.0, "by_category": {}, "leaks": []}
    for item in corpus:
        t0 = time.perf_counter()
        out, blocked = adapter.mask(item["text"])
        r["ms"] += (time.perf_counter() - t0) * 1000
        cat = r["by_category"].setdefault(item["category"], {"items": 0, "leaked": 0})
        cat["items"] += 1
        if not item["planted"]:
            r["negatives"] += 1
            if blocked or out != item["text"]:
                r["false_alarms"] += 1
                cat["leaked"] += 1  # for negatives this column counts false alarms
            continue
        r["items"] += 1
        r["values_total"] += len(item["planted"])
        r["blocked"] += blocked
        leaked = [] if blocked else [v for v in item["planted"] if v in out]
        if leaked:
            r["items_leaked"] += 1
            r["values_leaked"] += len(leaked)
            cat["leaked"] += 1
            r["leaks"].append({"id": item["id"], "values": leaked})
    r["ms_per_item"] = round(r["ms"] / len(corpus), 1)
    del r["ms"]
    return r


def main(names):
    corpus = [json.loads(l) for l in (HERE / os.environ.get("CORPUS", "corpus.jsonl")).read_text().splitlines()]
    names = names or sorted(p.stem for p in (HERE / "adapters").glob("*.py") if not p.stem.startswith("_"))
    results = [score(importlib.import_module(n), corpus) for n in names]
    (HERE / os.environ.get("CORPUS", "corpus.jsonl").replace(".jsonl", "_results.json")).write_text(json.dumps(results, indent=2))
    print(f"{'adapter':<28}{'items leaked':>14}{'values leaked':>16}{'blocked':>9}{'false alarms':>14}{'ms/item':>9}")
    for r in results:
        print(f"{r['adapter']:<28}{r['items_leaked']:>7}/{r['items']:<6}{r['values_leaked']:>8}/{r['values_total']:<7}"
              f"{r['blocked']:>9}{r['false_alarms']:>8}/{r['negatives']:<5}{r['ms_per_item']:>9}")


if __name__ == "__main__":
    main(sys.argv[1:])
