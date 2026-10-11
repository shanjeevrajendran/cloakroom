"""Turn a holdout delivery (markdown with JSON Lines in code blocks) into a bench corpus, and validate it.

    python bench/import_holdout.py DIGEST.md|PART.jsonl [...] [--out holdout_v2.jsonl] [--expect 120,40]

Takes delivered .jsonl files as they are (one per batch is fine), or pulls every JSON line out of a digest's
code blocks, then checks: exact keys, unique ids, counts
(total and negatives), every planted value an exact substring of its text, negatives with no planted
values, no planted value that also sits in a negative. Writes the corpus only when every check passes;
otherwise prints each problem and exits 1. Never prints planted values (they look like real secrets).
"""
import argparse
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
KEYS = {"id", "category", "text", "planted"}


REPAIRED = []


def extract(md, repair_escapes=False):
    # Slack may glue fences onto the data (```{"id": ...} and ...}```), so read line by line
    items, bad = [], 0
    for line in md.splitlines():
        line = next((p for p in line.split("```") if p.strip().startswith('{"id"')), "").strip()  # drops fences + footers
        line = re.sub(r"\}\s*\*?Sent using\*?.*$", "}", line)  # a "Sent using ..." footer glued after the object
        # Slack auto-links any scheme (<postgres://u:p@h/db>), and the collector only undoes http/mailto/tel
        line = re.sub(r"<([a-z][a-z0-9+.-]*://[^|>]+)(?:\|[^>]*)?>", r"\1", line)
        if not line.startswith('{"id"'):
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            if repair_escapes and '\\\\"' in line:
                try:  # over-escaped quotes (\\" for \"): undo uniformly, keep only if it then parses
                    item = json.loads(line.replace('\\\\"', '\\"'))
                    items.append(item)
                    REPAIRED.append(item.get("id"))
                    continue
                except json.JSONDecodeError:
                    pass
            bad += 1
    return items, bad


def validate(items, bad, total, negatives):
    errs = []
    if bad:
        errs.append(f"{bad} lines are not valid JSON")
    ids = [i.get("id") for i in items]
    dup = {x for x in ids if ids.count(x) > 1}
    if dup:
        errs.append(f"duplicate ids: {sorted(dup)[:10]}")
    for i in items:
        if set(i) != KEYS:
            errs.append(f"{i.get('id')}: keys {sorted(i)}")
            continue
        if not isinstance(i["planted"], list) or not isinstance(i["text"], str):
            errs.append(f"{i['id']}: wrong types")
            continue
        missing = [n for n, v in enumerate(i["planted"]) if v not in i["text"]]
        if missing:
            errs.append(f"{i['id']}: planted values #{missing} are not exact substrings of text")
        if i["category"] == "negative" and i["planted"]:
            errs.append(f"{i['id']}: negative with planted values")
        if i["category"] != "negative" and not i["planted"]:
            errs.append(f"{i['id']}: non-negative with no planted values")
    negs = [i for i in items if i.get("planted") == []]
    if len(items) != total:
        errs.append(f"{len(items)} items, expected {total}")
    if len(negs) != negatives:
        errs.append(f"{len(negs)} negatives, expected {negatives}")
    planted = {v for i in items for v in i.get("planted") or []}
    for n in negs:
        hits = sum(v in n["text"] for v in planted)
        if hits:
            errs.append(f"{n['id']}: negative contains {hits} value(s) planted elsewhere")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="digest .md and/or delivered .jsonl files")
    ap.add_argument("--out", default="holdout_v2.jsonl")
    ap.add_argument("--expect", default="120,40", help="total,negatives")
    ap.add_argument("--repair-escapes", action="store_true",
                    help="undo over-escaped quotes (\\\\\" for \\\") on lines that otherwise fail to parse; "
                         "every planted value must still be an exact substring")
    ap.add_argument("--corrections", action="store_true",
                    help="a later line with the same id replaces the earlier one (a correction reply); "
                         "broken lines that a correction replaces are not counted as errors")
    a = ap.parse_args()
    total, negatives = map(int, a.expect.split(","))
    items, bad, bad_ids = [], 0, []
    for path in a.inputs:
        text = pathlib.Path(path).read_text(encoding="utf-8")
        got, nbad = extract(text if path.endswith(".md") else "```\n" + text + "\n```", a.repair_escapes)
        items, bad = items + got, bad + nbad
        bad_ids += re.findall(r'^\W*\{"id": "([\w-]+)"', text, re.M)
    if a.corrections:
        latest = {}
        for i in items:
            latest[i.get("id")] = i  # later wins
        replaced = len(items) - len(latest)
        good = {i.get("id") for i in items}
        bad = sum(1 for x in set(bad_ids) if x not in good)  # broken lines with no valid version anywhere
        items = list(latest.values())
        print(f"corrections: {replaced} earlier lines replaced by a later version")
    errs = validate(items, bad, total, negatives)
    n_vals = sum(len(i.get("planted") or []) for i in items)
    print(f"{len(items)} items, {sum(i.get('planted') == [] for i in items)} negatives, {n_vals} planted values")
    if REPAIRED:
        print(f"repaired over-escaped quotes on {len(REPAIRED)} lines: {' '.join(REPAIRED)}")
    if errs:
        print("FAIL")
        for e in errs:
            print(" -", e)
        sys.exit(1)
    (HERE / a.out).write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))
    print(f"PASS -> bench/{a.out}")


if __name__ == "__main__":
    main()
