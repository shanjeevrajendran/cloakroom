"""Cloakroom on public PII datasets: recall per label, precision per mask. Run once, no tuning.

The datasets are downloaded separately and never committed (ai4privacy's license forbids redistribution):
  DATA/gretel/English_test.parquet       gretelai/synthetic_pii_finance_multilingual (Apache-2.0)
  DATA/ai4privacy/english_validation.jsonl ai4privacy/pii-masking-300k, validation, English (non-commercial research)
  DATA/tab/repo/echr_test.json           NorskRegnesentral/text-anonymization-benchmark (MIT)

Scoring, per dataset label mapped to a scope:
  must    should be masked: recall counts it; a value only partly masked is a leak
  neutral Cloakroom doesn't aim at it (company, city, plain date...): masking it is neither right nor wrong
  nomask  labelled "don't mask" (TAB): masking it is a false alarm
A mask (finding) is right if it overlaps a must span, neutral if it overlaps only neutral spans, and a false alarm if
it overlaps nothing labelled or only nomask spans. precision = right / (right + false alarms).

    python bench/public/run_public.py DATA [--mode full|rules] [--n 1000] [--split test|dev]
Writes the summary to stdout and examples (values!) to DATA/results/, never to the repo.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import random
import time

AI4P = {"must": {"USERNAME", "IDCARD", "EMAIL", "SOCIALNUMBER", "PASSPORT", "DRIVERLICENSE", "LASTNAME1", "LASTNAME2",
                 "LASTNAME3", "GIVENNAME1", "GIVENNAME2", "BOD", "TEL", "STREET", "PASS"}}
GRETEL = {"must": {"name", "first_name", "last_name", "email", "phone_number", "street_address", "date_of_birth", "ssn",
                   "passport_number", "driver_license_number", "credit_card_number", "credit_card_security_code",
                   "account_pin", "iban", "bban", "bank_routing_number", "customer_id", "employee_id", "user_name",
                   "password", "api_key"}}


def load_ai4privacy(data: pathlib.Path):
    for line in (data / "ai4privacy/english_validation.jsonl").read_text().splitlines():
        r = json.loads(line)
        yield {"id": f"a4p-{r['id']}", "text": r["source_text"],
               "gold": [(m["start"], m["end"], m["label"], "must" if m["label"] in AI4P["must"] else "neutral")
                        for m in r["privacy_mask"]]}


def load_gretel(data: pathlib.Path):
    import pyarrow.parquet as pq
    for i, r in enumerate(pq.read_table(data / "gretel/English_test.parquet").to_pylist()):
        yield {"id": f"gre-{i}", "text": r["generated_text"],
               "gold": [(s["start"], s["end"], s["label"], "must" if s["label"] in GRETEL["must"] else "neutral")
                        for s in json.loads(r["pii_spans"])]}


def load_tab(data: pathlib.Path, split: str = "test"):
    for d in json.loads((data / f"tab/repo/echr_{split}.json").read_text()):
        ann = next(iter(d["annotations"].values()))["entity_mentions"]  # first annotator
        scope = {"DIRECT": "must", "QUASI": "neutral", "NO_MASK": "nomask"}
        yield {"id": f"tab-{d['doc_id']}", "text": d["text"],
               "gold": [(m["start_offset"], m["end_offset"], f"{m['entity_type']}/{m['identifier_type']}",
                         scope[m["identifier_type"]]) for m in ann]}


LOADERS = {"gretel": load_gretel, "ai4privacy": load_ai4privacy, "tab": load_tab}


def score(items, detect):
    by_label = collections.defaultdict(lambda: [0, 0])  # label -> [caught, total]
    right = neutral = false_alarms = 0
    fn, fp = [], []
    ms = 0.0
    for it in items:
        text = it["text"]
        t0 = time.perf_counter()
        findings = detect(text)
        ms += (time.perf_counter() - t0) * 1000
        spans = [(f.start, f.end, f.kind) for f in findings]
        for s, e, label, scope in it["gold"]:
            if scope != "must" or e <= s:
                continue
            covered = sorted((max(a, s), min(b, e)) for a, b, _ in spans if a < e and b > s)
            pos, ok = s, True
            for a, b in covered:  # every non-space character of the value must be under some mask
                if a > pos and text[pos:a].strip():
                    ok = False
                    break
                pos = max(pos, b)
            ok = ok and not text[pos:e].strip()
            by_label[label][0] += ok
            by_label[label][1] += 1
            if not ok:
                fn.append({"id": it["id"], "label": label, "value": text[s:e],
                           "context": text[max(0, s - 60):e + 60]})
        for a, b, kind in spans:
            hits = [scope for s, e, _, scope in it["gold"] if a < e and b > s]
            if "must" in hits:
                right += 1
            elif hits and all(h == "neutral" for h in hits):
                neutral += 1
            else:
                false_alarms += 1
                fp.append({"id": it["id"], "kind": kind, "value": text[a:b], "context": text[max(0, a - 60):b + 60],
                           "labelled": hits})
    caught = sum(c for c, _ in by_label.values())
    total = sum(t for _, t in by_label.values())
    recall = caught / total if total else 0.0
    precision = right / (right + false_alarms) if right + false_alarms else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"items": len(items), "must_values": total, "caught": caught, "recall": round(recall, 3),
            "masks": right + neutral + false_alarms, "right": right, "neutral": neutral, "false_alarms": false_alarms,
            "precision": round(precision, 3), "f1": round(f1, 3), "ms_per_item": round(ms / max(1, len(items)), 1),
            "recall_by_label": {k: f"{c}/{t}" for k, (c, t) in sorted(by_label.items(), key=lambda kv: kv[1][0] / kv[1][1])},
            "fp_by_kind": dict(collections.Counter(x["kind"] for x in fp).most_common())}, fn, fp


def main():
    p = argparse.ArgumentParser()
    p.add_argument("data", type=pathlib.Path)
    p.add_argument("--mode", default="full", choices=["full", "rules"])
    p.add_argument("--n", type=int, default=1000, help="items per dataset (random, seed 7); TAB uses all")
    p.add_argument("--only", nargs="*", default=list(LOADERS))
    p.add_argument("--split", default="test", choices=["test", "dev"],
                   help="test: the first sample (seed 7), kept as the holdout; dev: other rows (seed 11) / TAB dev, for fixing")
    a = p.parse_args()
    from cloakroom import engine
    out = a.data / "results"
    out.mkdir(exist_ok=True)
    summary = {}
    for name in a.only:
        if name == "tab":
            items = list(load_tab(a.data, a.split))
        else:
            items = list(LOADERS[name](a.data))
            test = random.Random(7).sample(items, min(a.n, len(items)))
            if a.split == "dev":
                taken = {it["id"] for it in test}
                rest = [it for it in items if it["id"] not in taken]
                test = random.Random(11).sample(rest, min(a.n, len(rest)))
            items = test
        r, fn, fp = score(items, lambda t: engine.detect(t, a.mode)[0])
        summary[name] = r
        tag = f"{name}_{a.mode}" + ("_dev" if a.split == "dev" else "")
        (out / f"{tag}_misses.json").write_text(json.dumps(fn, indent=1, ensure_ascii=False))
        (out / f"{tag}_false_alarms.json").write_text(json.dumps(fp, indent=1, ensure_ascii=False))
        print(f"{name:<11} {a.mode:<5} recall {r['recall']:.3f} ({r['caught']}/{r['must_values']})  precision "
              f"{r['precision']:.3f} ({r['false_alarms']} false alarms of {r['masks']} masks)  F1 {r['f1']:.3f}  "
              f"{r['ms_per_item']} ms/item", flush=True)
    (out / f"summary_{a.mode}{'_dev' if a.split == 'dev' else ''}.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
