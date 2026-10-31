#!/usr/bin/env python3
"""Release completeness check: the newest CHANGELOG entry's features must show up wherever a reader looks.

    python3 scripts/release_complete.py [--root DIR]

A feature is a `(#anchor)` link or a backticked `cloakroom <cmd>` in the newest CHANGELOG entry. Only commands that no
older entry mentions count as new. Each failure prints the place and the missing name; exit 1 on any failure.
"""
import argparse
import pathlib
import re
import sys

ENTRY = re.compile(r"^## (\d+\.\d+\.\d+) \((\d{4}-\d{2}-\d{2}|unreleased)\)\s*$", re.M)
CMD = re.compile(r"`cloakroom ([a-z][a-z-]*)")
ANCHOR = re.compile(r"\(#([a-z0-9-]+)\)")


def slug(heading):  # GitHub's anchor for a heading
    return re.sub(r"[^a-z0-9 -]", "", heading.lower()).strip().replace(" ", "-")


def section(text, start, end):
    i = text.find(start)
    if i < 0:
        return None
    j = text.find(end, i + len(start))
    return text[i:j] if j >= 0 else text[i:]


def mentioned(region, name, is_anchor):
    return f"(#{name})" in region if is_anchor else bool(re.search(rf"cloakroom {re.escape(name)}\b", region))


def check(root):
    """Return (messages, failures): messages are 'ok'/'note' lines, failures are 'place: missing name'."""
    root = pathlib.Path(root)
    out, bad = [], []
    read = lambda p: (root / p).read_text(encoding="utf-8") if (root / p).is_file() else None
    log, readme, limits = read("CHANGELOG.md"), read("README.md"), read("docs-LIMITS.md")
    pyproject, landing, llms = read("pyproject.toml"), read("docs/index.html"), read("llms.txt")
    if log is None or readme is None:
        return out, ["CHANGELOG.md or README.md: file missing"]
    heads = list(ENTRY.finditer(log))
    if not heads:
        return out, ["CHANGELOG.md: no '## x.y.z (YYYY-MM-DD)' or '(unreleased)' entry"]
    version = heads[0].group(1)
    entry = log[heads[0].end():heads[1].start() if len(heads) > 1 else len(log)]
    older = log[heads[1].start():] if len(heads) > 1 else ""
    anchors = sorted(set(ANCHOR.findall(entry)))
    all_cmds = sorted(set(CMD.findall(entry)))
    new_cmds = [c for c in all_cmds if not re.search(rf"`cloakroom {re.escape(c)}\b", older)]
    feats = [(a, True) for a in anchors] + [(c, False) for c in new_cmds]
    label = lambda n, a: f"(#{n})" if a else f"cloakroom {n}"
    out.append(f"note: {version}: anchors {anchors or 'none'}, new commands {new_cmds or 'none'}, "
               f"all commands {all_cmds or 'none'}")

    def need(tag, place, region, items):
        if region is None:
            bad.append(f"{tag} {place}: section not found")
            return
        miss = [label(n, a) for n, a in items if not mentioned(region, n, a)]
        bad.extend(f"{tag} {place}: missing {m}" for m in miss)
        if not miss:
            out.append(f"ok {tag} {place}")

    need("(a)", "README top feature list", readme.split("\n## What works where")[0] if "\n## What works where" in readme
         else None, feats)
    recent = section(readme, "## Recent releases", "\n## ")
    newest = None
    if recent is not None:
        m = re.search(r"^- \*\*(.+?)\*\*.*?(?=^- \*\*|\Z)", recent, re.M | re.S)
        newest = m.group(0) if m else ""
        if not newest.startswith(f"- **{version}**"):
            bad.append(f"(b) README Recent releases: newest entry is not {version}")
    need("(b)", "README Recent releases newest entry", newest, feats)
    heads_md = {slug(h) for h in re.findall(r"^## (.+)$", readme, re.M)}
    miss = [f"## heading for (#{a})" for a in anchors if a not in heads_md]
    bad.extend(f"(c) README: missing {m}" for m in miss)
    if not miss:
        out.append("ok (c) README headings")
    # (d) a command reference is any README section other than the intro and Recent releases (own section or the list)
    body = "\n## ".join(s for s in readme.split("\n## ")[1:] if not s.startswith("Recent releases"))
    need("(d)", "README sections", body, [(c, False) for c in all_cmds])
    if llms and CMD.search(llms):
        need("(d)", "llms.txt", llms, [(c, False) for c in all_cmds])
    else:
        out.append("note: (d) llms.txt lists no commands, skipped")
    cards = section(landing, '<section class="features"', "</section>") if landing else None
    if cards is None:
        out.append("note: (e) docs/index.html has no features list, skipped")
    else:
        titles = {slug(h): h for h in re.findall(r"^## (.+)$", readme, re.M)}  # anchor or exact section title
        miss = [f"(#{a})" for a in anchors if f"#{a}" not in cards and titles.get(a, a) not in cards]
        miss += [f"cloakroom {c}" for c in new_cmds if not mentioned(cards, c, False)]
        bad.extend(f"(e) docs/index.html features: missing {m}" for m in miss)
        if not miss:
            out.append("ok (e) docs/index.html features")
    want = re.search(r'^version = "([^"]+)"', pyproject or "", re.M)
    if heads[0].group(2) == "unreleased":  # the version pins move at release time, not while the entry is written
        out.append(f"note: (f) CHANGELOG {version} is unreleased, version pin check skipped")
    elif not want or want.group(1) != version:
        bad.append(f"(f) pyproject.toml: version {want and want.group(1)} but newest CHANGELOG entry is {version}")
    else:
        out.append(f"ok (f) CHANGELOG {version} = pyproject (the other pins: version-init and version-pins steps)")
    # (h) the generated pages carry pyproject's version; llms.txt is built from install and security only, no version
    if want and heads[0].group(2) != "unreleased":
        stale = [p for p in ("docs/commands.html", "docs/releases.html")
                 if not re.search(rf"(?<![\d.]){re.escape(want.group(1))}(?![\d.])", read(p) or "")]
        bad.extend(f"(h) {p}: no {want.group(1)}; run: python scripts/gen_pages.py --local" for p in stale)
        if not stale:
            out.append(f"ok (h) generated pages show {want.group(1)}")
    limit_bullets = [b for b in re.split(r"\n(?=- )", entry) if b.lstrip("- ").lower().startswith("known limit")]
    ok = True
    for b in limit_bullets:
        ref = re.search(r'docs-LIMITS,? ["“]([^"”]+)["”]', " ".join(b.split()))
        if not ref:
            bad.append("(g) CHANGELOG Known limit: no (see docs-LIMITS, \"name\") pointer in: " + b[:60].strip())
        elif not any(ref.group(1).lower() in l.lower() for l in (limits or "").splitlines()):
            bad.append(f"(g) docs-LIMITS.md: missing line for \"{ref.group(1)}\"")
        else:
            continue
        ok = False
    if ok:
        out.append(f"ok (g) docs-LIMITS ({len(limit_bullets)} known limit(s))")
    return out, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=pathlib.Path(__file__).resolve().parents[1])
    out, bad = check(ap.parse_args().root)
    print("\n".join(out))
    if bad:
        print("\nFAIL release completeness:\n  " + "\n  ".join(bad))
        return 1
    print("release completeness ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
