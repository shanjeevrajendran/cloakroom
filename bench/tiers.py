"""Leaks by how much they matter, across the one-shot holdouts.

    python bench/tiers.py [holdout_v2 holdout_v3 ...]      default: holdout_v2 .. holdout_v6

Reads each holdout and its first-run results file (`<name>_results.json`, written by run_bench.py) and prints a
markdown table: per tool, leaked planted values by tier.

Tiers come from the shape of each planted value (the data does not label value types), so a few values can land
in a debatable tier; ambiguous shapes lean "critical", which only counts against every tool equally.
- critical: credentials (API keys and tokens, URL and keyed passwords, private keys, password-like strings),
  card numbers (Luhn), IBANs, government and health IDs (SSN, UK NI, Aadhaar, PAN, passport/licence/record numbers)
- high: names, emails, phone numbers, street addresses, dates of birth
- low: usernames, home-directory paths, IP addresses, key fingerprints
- fictional: phone numbers in the range reserved for fiction (555-0100..0199), reported apart
"""
import json
import pathlib
import re
import sys
from collections import defaultdict

HERE = pathlib.Path(__file__).parent
TIERS = ("critical", "high", "low", "fictional")
_MONTH = re.compile(r"(?i)\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b")


def _luhn(digits: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(digits)):
        n = int(c) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def tier(v: str) -> str:
    d = re.sub(r"\D", "", v)
    if re.search(r"555[\s.-]?01\d\d", v):
        return "fictional"
    if re.search(r"-----BEGIN|AKIA|ASIA|gh[pousr]_|github_pat_|sk[_-](?:live|test|proj|ant)|xox[abposr]-|AIza|"
                 r"eyJ[\w-]+\.|SG\.|^(?:AC|SK)[0-9a-f]{32}$|hf_|Bearer|://[^:@\s]+:[^@\s]+@", v):
        return "critical"
    if 13 <= len(d) <= 19 and _luhn(d) and re.fullmatch(r"[\d\s-]+", v):
        return "critical"                                    # card
    if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9 ]{10,30}", v):
        return "critical"                                    # IBAN
    if (re.fullmatch(r"\d{3}-\d{2}-\d{4}", v) or re.fullmatch(r"[A-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]", v)
            or re.fullmatch(r"\d{4}\s?\d{4}\s?\d{4}", v) or re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", v)):
        return "critical"                                    # SSN, UK NI, Aadhaar, PAN
    if re.fullmatch(r"[A-Z]{0,3}\d{6,9}", v) or re.fullmatch(r"[A-Za-z]{0,2}\d{3,4}-\d{4}-\d{3,4}", v):
        return "critical"                                    # passport, licence, record and account numbers
    if len(v) >= 8 and " " not in v and re.search(r"\d", v) and re.search(r"[A-Za-z]", v) and re.search(r"[^\w.@/-]", v):
        return "critical"                                    # password-like
    if ("@" in v or re.search(r"\d{3}[\s.)-]+\d{3}[\s.-]+\d{4}|\+\d", v)
            or re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}", v) or (_MONTH.search(v) and re.search(r"\d", v))):
        return "high"                                        # email, phone, date of birth
    if (re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?", v) or re.search(r"SHA256:|MD5:|^[A-Z]:\\|^/", v)
            or re.fullmatch(r"[a-z0-9._-]{2,32}", v)):
        return "low"                                         # IP, fingerprint, path, username
    return "high"                                            # names and street addresses


def main(names):
    names = names or [f"holdout_v{n}" for n in range(2, 7)]
    planted = defaultdict(int)
    leaked = defaultdict(lambda: defaultdict(int))
    for name in names:
        for line in (HERE / f"{name}.jsonl").read_text().splitlines():
            for v in json.loads(line)["planted"]:
                planted[tier(v)] += 1
        for r in json.loads((HERE / f"{name}_results.json").read_text()):
            for leak in r["leaks"]:
                for v in leak["values"]:
                    leaked[r["adapter"]][tier(v)] += 1
    print(f"Sets: {', '.join(names)}. Planted values: " + ", ".join(f"{t} {planted[t]}" for t in TIERS) + "\n")
    print("| Tool | Critical | High | Low | Fictional (reserved 555-01xx) |")
    print("|---|---|---|---|---|")
    for tool, d in sorted(leaked.items(), key=lambda x: (x[1]["critical"], x[1]["high"])):
        print(f"| {tool} | {d['critical']}/{planted['critical']} | {d['high']}/{planted['high']} | "
              f"{d['low']}/{planted['low']} | {d['fictional']}/{planted['fictional']} |")


if __name__ == "__main__":
    main(sys.argv[1:])
