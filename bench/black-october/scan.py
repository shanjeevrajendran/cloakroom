"""Scan each arm's artifacts with gitleaks, trufflehog and an exact canary match; write out/results.md and .json.

Targets per arm:
  model_view   what the model was given: prompts, tool results, its own text (extracted from the transcript;
               tool_use inputs are left out because Claude Code logs them after the hook rewrote them)
  transcript   the whole local session log (~/.claude/projects/...), as an infostealer copying it would get it
  result       the `claude -p` JSON answer (what a gateway or CI log would keep)
  git_history  every commit in the fixture repo (what a push would publish)
"""
import json
import os
import subprocess
import sys
import tempfile

OUT = sys.argv[1]
ARMS = ("without", "with")


def model_view(tx):
    parts = []
    for line in open(tx):
        try:
            m = (json.loads(line).get("message") or {})
        except ValueError:
            continue
        content = m.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        for b in content or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "thinking":
                parts.append(b.get("thinking", ""))
            elif b.get("type") == "tool_result":
                c = b.get("content")
                parts.append(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False))
    return "\n".join(parts)


def gitleaks(path, git=False):
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        rep = f.name
    cmd = ["gitleaks", "git" if git else "dir", path, "--report-format", "json", "--report-path", rep,
           "--no-banner", "--exit-code", "0", "--log-level", "error"]
    subprocess.run(cmd, check=True, capture_output=True)
    found = json.load(open(rep)) or []
    os.unlink(rep)
    return found


def trufflehog(path, git=False):
    src = ["git", "file://" + path] if git else ["filesystem", path]
    r = subprocess.run(["trufflehog", *src, "--no-verification", "--no-update", "--json"],
                       capture_output=True, text=True, check=True)
    return [json.loads(x) for x in r.stdout.splitlines() if x.startswith("{")]


def canary_hits(text, canaries):
    return sorted(k for k, v in canaries.items() if v in text)


def main():
    res = {}
    for arm in ARMS:
        W = os.path.join(OUT, arm)
        canaries = json.load(open(os.path.join(W, "canaries.json")))
        scan_dir = os.path.join(W, "scan")
        os.makedirs(scan_dir, exist_ok=True)
        targets = {}
        tx = os.path.join(W, "transcript.jsonl")
        if os.path.exists(tx):
            for name, text in (("model_view", model_view(tx)), ("transcript", open(tx).read())):
                d = os.path.join(scan_dir, name)
                os.makedirs(d, exist_ok=True)
                open(os.path.join(d, name + ".txt"), "w").write(text)
                targets[name] = (d, text, False)
        rj = os.path.join(W, "result.json")
        if os.path.exists(rj):
            d = os.path.join(scan_dir, "result")
            os.makedirs(d, exist_ok=True)
            text = open(rj).read()
            open(os.path.join(d, "result.json"), "w").write(text)
            targets["result"] = (d, text, False)
        proj = os.path.join(W, "proj")
        hist = subprocess.run(["git", "-C", proj, "log", "-p", "--all"], capture_output=True, text=True).stdout
        targets["git_history"] = (proj, hist, True)
        res[arm] = {}
        for name, (path, text, git) in targets.items():
            g, t = gitleaks(path, git), trufflehog(path, git)
            res[arm][name] = {
                "gitleaks": len(g), "gitleaks_rules": sorted({x["RuleID"] for x in g}),
                "trufflehog": len(t), "trufflehog_detectors": sorted({x.get("DetectorName", "") for x in t}),
                "canaries": canary_hits(text, canaries), "of": len(canaries),
            }
        log = os.path.join(proj, "SETUP.md")
        res[arm]["setup_md_real_values"] = canary_hits(open(log).read(), canaries) if os.path.exists(log) else None
        commits = subprocess.run(["git", "-C", proj, "rev-list", "--count", "HEAD"], capture_output=True, text=True)
        res[arm]["commits"] = int(commits.stdout.strip() or 0)
        try:
            res[arm]["answer"] = json.load(open(rj)).get("result", "")[:400]
        except (OSError, ValueError):
            res[arm]["answer"] = None
    json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=2)
    rows = ["| Target | Arm | gitleaks | trufflehog | canaries found (exact) |", "|---|---|---|---|---|"]
    for name in ("model_view", "transcript", "result", "git_history"):
        for arm in ARMS:
            r = res[arm].get(name)
            if r:
                rows.append(f"| {name} | {arm} | {r['gitleaks']} | {r['trufflehog']} | {len(r['canaries'])}/{r['of']} |")
    rows.append("")
    for arm in ARMS:
        rows.append(f"- {arm}: commits in repo {res[arm]['commits']} (1 = only the fixture's init); "
                    f"SETUP.md real values {res[arm]['setup_md_real_values']}")
    open(os.path.join(OUT, "results.md"), "w").write("\n".join(rows) + "\n")
    print("\n".join(rows))


if __name__ == "__main__":
    main()
