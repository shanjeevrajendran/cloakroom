"""Generate docs/commands.html, docs/releases.html and docs/llms.txt for cloakroom.sh from the released program.

Installs cloakroom==VERSION into a throwaway venv and reads every command's --help; reads CHANGELOG.md, the
handbook and the README from the public repo at the release tag (never a local checkout). Page chrome (head,
nav, footer) is copied from docs/install.html, so every page shares it. Same inputs, same bytes out.
    python3 scripts/gen_pages.py           the live-site workflow: the published release, from PyPI and its tag
    python3 scripts/gen_pages.py --local   before publishing: this checkout's version, sources and the
                                           `cloakroom` on PATH (activate the checkout's venv first)
"""
import argparse
import html
import shutil
from html.parser import HTMLParser
import pathlib
import re
import subprocess
import sys
import tempfile

VERSION = "0.9.0"
REPO = "https://github.com/shanjeevrajendran/cloakroom"
VENV = pathlib.Path("/tmp/ck-venv")
ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SOURCES = ("CHANGELOG.md", "docs-HANDBOOK.md", "README.md")

# Typed by the machine or once at most (the handbook's "Other commands" line, the hook entry points).
RARE = ["export-onnx", "status", "serve", "clean-history", "hook", "git-check"]

# The handbook's one-liners for commands whose --help has no description. Checked against the source below.
PURPOSE = {
    "check": "what would be masked, by kind; never prints the values",
    "explain": "every finding with the rule that found it, and what was skipped as safe",
    "mask": "the masked text, as Claude would see it",
    "status": "mode, service, vault sessions",
    "serve": "start the local service by hand; it normally starts on first use",
}

# When to run it, word for word from the handbook or the README; a command missing here has no stated moment.
WHEN = {
    "doctor": "Every line should say `ok`, except `service: down`, which is normal before the first use.",
    "check": "The same engine works on any file or pipe, which is handy for checking what it would do",
    "explain": "run `cloakroom explain` on the file to see which rule fired and add an `allow` entry.",
    "give": "When the agent needs a key (a Stripe key, a database password), don't paste it into a prompt.",
    "dashboard": "when it masks something harmless (a false alarm), mark it once in the dashboard and a rule stops it next time.",
    "update": "Codex, Gemini CLI, Cursor, git commits, or a `cloakroom` you installed yourself: run `cloakroom update` in a terminal.",
    "export-onnx": "Any other machine (Intel Mac, most Windows and Linux)",
    "exposure": "lists the keys and passwords your coding agents saw, so you know what to rotate.",
}


def run(*cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


def fetch_sources():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ck-src-"))
    run("git", "clone", "-q", "--depth", "1", "--branch", f"v{VERSION}", REPO + ".git", str(tmp / "ck"))
    return {n: (tmp / "ck" / n).read_text() for n in SOURCES}


def local_cloakroom():
    exe = shutil.which("cloakroom")
    if not exe:
        sys.exit("--local needs `cloakroom` on PATH: activate this checkout's venv (pip install -e .)")
    got = run(exe, "--version").split()[-1]
    if got != VERSION:
        sys.exit(f"--local: `cloakroom` on PATH is {got}, pyproject.toml says {VERSION}")
    return lambda *a: run(exe, *a, env={"COLUMNS": "1000", "PATH": "/usr/bin:/bin"})


def cloakroom():
    exe = VENV / "bin" / "cloakroom"
    have = exe.exists() and run(str(exe), "--version").split()[-1] == VERSION
    if not have:
        run(sys.executable, "-m", "venv", "--clear", str(VENV))
        run(str(VENV / "bin" / "pip"), "install", "-q", f"cloakroom=={VERSION}")
    # a wide terminal keeps argparse from wrapping, so each description is one line
    return lambda *a: run(str(exe), *a, env={"COLUMNS": "1000", "PATH": "/usr/bin:/bin"})


def norm(s):
    return " ".join(s.replace("**", "").split())


def inline(md):
    """Markdown inline to HTML: `code`, **bold**, [text](link); everything else escaped."""
    codes = []
    def stash(m):
        codes.append("<code>" + html.escape(m.group(1), quote=False) + "</code>")
        return f"\x00{len(codes) - 1}\x00"
    s = html.escape(re.sub(r"`([^`]+)`", stash, md), quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    def link(m):
        href = m.group(2)
        if not href.startswith("http"):
            href = f"{REPO}/blob/main/{href}"
        return f'<a href="{html.escape(href)}">{m.group(1)}</a>'
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, s)
    return re.sub("\x00(\\d+)\x00", lambda m: codes[int(m.group(1))], s)


def chrome(page, title, description):
    src = (DOCS / "install.html").read_text()
    head, rest = src.split('<main id="main">')
    foot = rest.split("</main>")[1]
    swaps = [
        ("<title>Install Cloakroom</title>", f"<title>{title}</title>", 1),
        ('content="Install Cloakroom"', f'content="{title}"', 1),
        ('https://cloakroom.sh/install.html', f"https://cloakroom.sh/{page}", 2),
        ('<a href="install.html" aria-current="page">', '<a href="install.html">', 1),
        (f'<a href="{page}">', f'<a href="{page}" aria-current="page">', int(f'<a href="{page}">' in head)),
    ]
    old_desc = re.search(r'<meta name="description" content="([^"]+)">', head).group(1)
    swaps.insert(0, (old_desc, html.escape(description), 2))
    for a, b, n in swaps:
        assert head.count(a) == n, (page, a)
        head = head.replace(a, b)
    foot = foot.replace('\n<script src="install.js"></script>', "")
    return head + '<main id="main">\n', "</main>" + foot


def commands(src, ck):
    top = ck("--help")
    names = re.search(r"\{([a-z,-]+)\}", top).group(1).split(",")
    helps = dict(re.findall(r"^    ([a-z-]+) +(\S.*)$", top, re.M))
    blob = norm(src["docs-HANDBOOK.md"] + "\n" + src["README.md"])
    log = src["CHANGELOG.md"]
    sections = re.split(r"^## ", log, flags=re.M)[1:]
    rows = {}
    for name in names:
        usage = norm(ck(name, "--help").split("\n\n")[0]).removeprefix("usage: ").replace(" [-h]", "")
        purpose = helps.get(name) or PURPOSE[name]
        assert name in helps or norm(PURPOSE[name]) in blob, name
        when = WHEN.get(name, "")
        assert norm(when) in blob, name
        first = [s.split("\n")[0] for s in sections if re.search(rf"`cloakroom {name}(?![\w-])", s)]
        arrived = first[-1].split(" (")[0] if first else "early release"
        args = usage.removeprefix(f"cloakroom {name}").strip()
        toks = " ".join(f'<span class="tok">{html.escape(t)}</span>' for t in re.findall(r"\[[^\]]*\]|\{[^}]*\}|\S+", args))
        cmd = f"cloakroom {name}" + (f' <span class="arg">{toks}</span>' if args else "")
        rows[name] = (
            f'<tr><td><code>{cmd}</code></td><td data-label="What it does">{inline(purpose)}</td>'
            f'<td data-label="When">{inline(when)}</td><td class="rel" data-label="First in the changelog">{arrived}</td></tr>'
        )
    assert set(RARE) <= set(rows), set(RARE) - set(rows)

    def table(group):
        return (
            '  <div class="table-wrap ref">\n    <table>\n'
            '      <colgroup><col class="c-cmd"><col><col><col class="c-rel"></colgroup>\n'
            '      <thead><tr><th scope="col">Command</th><th scope="col">What it does</th>'
            '<th scope="col">When to run it</th><th scope="col">First in the changelog</th></tr></thead>\n      <tbody>\n'
            + "".join(f"        {rows[n]}\n" for n in group)
            + "      </tbody>\n    </table>\n  </div>\n"
        )

    head, foot = chrome("commands.html", "Cloakroom commands",
                        f"Every cloakroom command in release {VERSION}: what it does, when to run it and the release it arrived in.")
    body = (
        '<section class="hero page-hero page-hero--short container" aria-labelledby="page-title">\n'
        '  <h1 id="page-title">Commands</h1>\n'
        f'  <p class="lead">Every command in Cloakroom {VERSION}, from its own help text.</p>\n'
        "</section>\n\n"
        '<section class="sec container" id="typed" aria-labelledby="typed-title">\n'
        '  <h2 id="typed-title">Commands you type</h2>\n'
        '  <div class="ref-body">\n'
        + table([n for n in names if n not in RARE])
        + "  </div>\n</section>\n\n"
        '<section class="sec container" id="rare" aria-labelledby="rare-title">\n'
        '  <h2 id="rare-title">Rarely typed</h2>\n'
        '  <div class="ref-body">\n'
        + table(RARE)
        + f'  <p class="src-note">Generated from <code>cloakroom --help</code> of release {VERSION}, '
        f'<a href="{REPO}/blob/v{VERSION}/docs-HANDBOOK.md">the handbook</a> and '
        f'<a href="{REPO}/blob/v{VERSION}/CHANGELOG.md">CHANGELOG.md</a>.</p>\n'
        "  </div>\n</section>\n\n"
    )
    return head + body + foot


def releases(src):
    out = []
    for block in re.split(r"^## ", src["CHANGELOG.md"], flags=re.M)[1:]:
        title, *lines = block.rstrip().split("\n")
        m = re.match(r"(\S+(?: - \S+)?)(?: \((.+)\))?$", title)
        version, date = m.group(1), m.group(2)
        items, parts = [], []
        for line in lines:
            if line.startswith("- "):
                items.append(line[2:])
            elif line.startswith("  ") and items:
                items[-1] += " " + line.strip()
            else:
                if items:
                    parts.append("<ul>\n" + "".join(f"      <li>{inline(i)}</li>\n" for i in items) + "    </ul>")
                    items = []
                text = line.strip()
                if text.startswith("### ") or re.fullmatch(r"\*\*[^*]+\*\*", text):
                    parts.append(f"<h3>{inline(text.lstrip('# ').strip('*'))}</h3>")
                elif text:
                    if parts and parts[-1].startswith("<p>"):
                        parts[-1] = parts[-1][:-4] + " " + inline(text) + "</p>"
                    else:
                        parts.append(f"<p>{inline(text)}</p>")
        if items:
            parts.append("<ul>\n" + "".join(f"      <li>{inline(i)}</li>\n" for i in items) + "    </ul>")
        when = ""
        if date and re.fullmatch(r"\d{4}-\d\d-\d\d", date):
            when = f'\n    <p><time datetime="{date}">{date}</time></p>'
        elif date:
            when = f"\n    <p>{inline(date)}</p>"
        vid = "v" + re.sub(r"[^0-9]+", "-", version).strip("-")
        out.append(
            f'  <article class="release" id="{vid}" aria-labelledby="{vid}-t">\n'
            f'    <div><h2 id="{vid}-t">{html.escape(version)}</h2>{when}</div>\n'
            '    <div class="release-body">\n    ' + "\n    ".join(parts) + "\n    </div>\n  </article>\n"
        )
    head, foot = chrome("releases.html", "Cloakroom releases",
                        "Every Cloakroom release, newest first, with its date and changes, from CHANGELOG.md.")
    return (
        head
        + '<section class="hero page-hero page-hero--short container" aria-labelledby="page-title">\n'
        '  <h1 id="page-title">Releases</h1>\n'
        '  <p class="lead">Every release, newest first, from CHANGELOG.md.</p>\n'
        "</section>\n\n"
        '<section class="sec container" aria-label="Changelog">\n'
        '  <div class="rel-list">\n' + "".join(out) + "  </div>\n</section>\n\n"
        + foot
    )


class Text(HTMLParser):
    """The <main> of a page as plain text: heads as ## lines, list items as - lines, code blocks kept as typed."""
    BLOCK = {"p", "h1", "h2", "h3", "li", "pre", "tr", "dt", "dd", "section"}

    def __init__(self):
        super().__init__()
        self.out, self.on, self.skip, self.pre, self.tabs, self.tab = [], False, 0, False, {}, None

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.on = True
        if not self.on:
            return
        a = dict(attrs)
        if tag == "button":
            self.skip += 1
            if a.get("role") == "tab":
                self.tab = a["aria-controls"]
                self.tabs[self.tab] = ""
        if a.get("role") == "tabpanel":
            self.out.append(f"\n\n#### {self.tabs[a['id']]}\n")
        if tag == "p" and self.out and self.out[-1] == "- ":
            return
        if tag in self.BLOCK:
            self.out.append("\n\n" if tag in {"h2", "h3", "section", "pre"} else "\n")
            self.out.append({"h1": "# ", "h2": "## ", "h3": "### ", "li": "- ", "dt": "- "}.get(tag, ""))
        if tag in {"td", "th"}:
            self.out.append(" | ")
        if tag == "pre":
            self.out.append("```\n")
            self.pre = True

    def handle_endtag(self, tag):
        if tag == "main":
            self.on = False
        if tag == "button":
            self.skip -= 1
            self.tab = None
        if tag == "pre":
            self.out.append("\n```\n")
            self.pre = False

    def handle_data(self, data):
        if self.tab:
            self.tabs[self.tab] += data
        if self.on and not self.skip:
            self.out.append(data if self.pre else re.sub(r"\s+", " ", data))

    def text(self):
        s = "".join(self.out)
        s = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n[ \t]+(?=\S)", "\n", s))
        return re.sub(r"\n{3,}", "\n\n", s).strip()


def llms():
    def page_text(name):
        t = Text()
        t.feed((DOCS / name).read_text())
        return t.text()

    index = (DOCS / "index.html").read_text()
    about = re.search(r'<meta name="description" content="([^"]+)">', index).group(1)
    nav = re.search(r'<nav aria-label="Site">(.*?)</nav>', (DOCS / "install.html").read_text(), re.S).group(1)
    pages = [(name, label) for name, label in re.findall(r'<a href="([a-z0-9-]+\.html)"[^>]*>([^<]+)</a>', nav)]
    pages.append(("settings.html", "Settings and fixes"))
    lines = [
        "# Cloakroom",
        "",
        f"> {html.unescape(about)}",
        "",
        "## Pages",
        "",
        "- [Home](https://cloakroom.sh/)",
        *[f"- [{label}](https://cloakroom.sh/{name})" for name, label in pages],
        f"- [Source code]({REPO})",
        "",
    ]
    for name in ("install.html", "security.html"):
        body = re.sub(r"^# ", "## ", page_text(name), count=1)
        lines += [f"<!-- https://cloakroom.sh/{name} -->", body.replace("\n### ", "\n#### ").replace("\n## ", "\n### "), ""]
    return "\n".join(lines)


def main():
    global VERSION
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="use this checkout instead of the published release")
    if ap.parse_args().local:
        VERSION = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
        src, ck = {n: (ROOT / n).read_text() for n in SOURCES}, local_cloakroom()
    else:
        src, ck = fetch_sources(), cloakroom()
    (DOCS / "commands.html").write_text(commands(src, ck))
    (DOCS / "releases.html").write_text(releases(src))
    (DOCS / "llms.txt").write_text(llms())
    print("wrote docs/commands.html, docs/releases.html, docs/llms.txt")


if __name__ == "__main__":
    main()
