"""What is installed, for the plugin's upgrade check (bootstrap.sh --upgrade, bootstrap.ps1 -Upgrade).

    <python of the installed cloakroom's own environment> -I upgrade_probe.py PIN

prints one line: KIND VERSION EXTRAS PREFIX
  KIND     older | same | newer (than PIN) | dev (editable, from a source tree, a .dev or +local version: left alone)
           | missing (no cloakroom in this environment)
  EXTRAS   the extras whose packages are present (model, torch, ocr), comma-separated, or - for none
  PREFIX   the environment's folder (sys.prefix), to find the service it runs
Standard library only: it runs inside environments of old Cloakroom releases, on Python 3.10+.
"""
import json
import re
import sys
from importlib import metadata, util

EXTRAS = (("model", ("onnxruntime", "tokenizers")), ("torch", ("torch",)), ("ocr", ("rapidocr",)))
_PRE = re.compile(r"[-_.]?(a|alpha|b|beta|c|rc|pre|preview)[-_.]?(\d*)$")
_POST = re.compile(r"[-_.]?(post|rev|r)[-_.]?(\d*)$")


def parse(v: str):
    """(release tuple, (tag, n)): tag -1 pre-release, 0 final, 1 post-release. None when it isn't a plain release."""
    m = re.match(r"v?(\d+(?:\.\d+)*)(.*)$", v.strip().lower())
    if not m:
        return None
    release, rest = tuple(int(x) for x in m.group(1).split(".")), m.group(2)
    if not rest:
        return release, (0, 0)
    for tag, rx in ((-1, _PRE), (1, _POST)):
        t = rx.match(rest)
        if t:
            return release, (tag, int(t.group(2) or 0))
    return None


def compare(a: str, b: str) -> int:
    """-1, 0 or 1 as version a is older than, the same as or newer than b. ValueError if either isn't a release."""
    pa, pb = parse(a), parse(b)
    if pa is None or pb is None:
        raise ValueError(f"not a release version: {a if pa is None else b}")
    n = max(len(pa[0]), len(pb[0]))
    ka = (pa[0] + (0,) * (n - len(pa[0])), pa[1])
    kb = (pb[0] + (0,) * (n - len(pb[0])), pb[1])
    return (ka > kb) - (ka < kb)


def is_dev(version: str, direct_url: str | None) -> bool:
    """An install the user builds themselves: editable, from a source folder, or a .dev / +local version."""
    if ".dev" in version or "+" in version or parse(version) is None:
        return True
    try:
        d = json.loads(direct_url) if direct_url else {}
    except ValueError:
        return True
    return bool(d.get("dir_info", {}).get("editable")) or "dir_info" in d


def kind(version: str, direct_url: str | None, pin: str) -> str:
    if is_dev(version, direct_url):
        return "dev"
    return ("older", "same", "newer")[compare(version, pin) + 1]


def extras(find=util.find_spec) -> str:
    have = [name for name, mods in EXTRAS if all(find(m) is not None for m in mods)]
    return ",".join(have) or "-"


def main(pin: str) -> str:
    try:
        dist = metadata.distribution("cloakroom")
    except metadata.PackageNotFoundError:
        return f"missing - - {sys.prefix}"
    return f"{kind(dist.version, dist.read_text('direct_url.json'), pin)} {dist.version} {extras()} {sys.prefix}"


if __name__ == "__main__":
    print(main(sys.argv[1]))
