"""scripts/release_complete.py: a tiny fake tree passes, and each check (a) to (g) fails when its place loses the name."""
import importlib.util
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "release_complete.py"
spec = importlib.util.spec_from_file_location("release_complete", SCRIPT)
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

FILES = {
    "pyproject.toml": 'version = "1.2.0"\n',
    "CHANGELOG.md": (
        "# Changelog\n\n## 1.2.0 (2026-11-01)\n"
        "- `cloakroom wave NAME`: waves (see [Waving](#waving)). Uses `cloakroom report` too.\n"
        "- Known limit: waves fade (see docs-LIMITS, \"Fading waves\").\n\n"
        "## 1.1.0 (2026-10-01)\n- `cloakroom report` counts things.\n"
    ),
    "README.md": (
        "Intro\n\n- `cloakroom wave NAME` waves ([Waving](#waving)).\n\n## What works where\n\nx\n\n"
        "## Recent releases\n\n- **1.2.0** (2026-11-01): `cloakroom wave NAME`. See [Waving](#waving).\n"
        "- **1.1.0** (2026-10-01): old.\n\n## Waving\n\n`cloakroom wave NAME`, `cloakroom report`\n"
    ),
    "docs/index.html": '<section class="features"><h3>Waving</h3><code>cloakroom wave</code></section>',
    "docs-LIMITS.md": "# Limits\n- Fading waves: they fade.\n",
    "llms.txt": "# x\nRun `cloakroom wave` or `cloakroom report`.\n",
}


def tree(tmp_path, **edits):
    for name, text in {**FILES, **edits}.items():
        if text is not None:
            (tmp_path / name).parent.mkdir(exist_ok=True)
            (tmp_path / name).write_text(text)
    return tmp_path


def failures(tmp_path, **edits):
    return rc.check(tree(tmp_path, **edits))[1]


def test_complete_tree_passes(tmp_path):
    out, bad = rc.check(tree(tmp_path))
    assert bad == [] and any(l.startswith("ok (e)") for l in out)


@pytest.mark.parametrize("tag, edits, missing", [
    ("(a)", {"README.md": FILES["README.md"].replace("- `cloakroom wave NAME` waves ([Waving](#waving)).\n", "")},
     "cloakroom wave"),
    ("(b)", {"README.md": FILES["README.md"].replace("`cloakroom wave NAME`. See [Waving](#waving).", "nothing")},
     "(#waving)"),
    ("(c)", {"README.md": FILES["README.md"].replace("## Waving", "## Other")}, "## heading for (#waving)"),
    ("(d)", {"README.md": FILES["README.md"].replace("`cloakroom wave NAME`, ", "")}, "cloakroom wave"),
    ("(d)", {"llms.txt": "# x\nRun `cloakroom other`.\n"}, "cloakroom wave"),
    ("(e)", {"docs/index.html": '<section class="features"><h3>Other</h3></section>'}, "cloakroom wave"),
    ("(f)", {"pyproject.toml": 'version = "1.1.0"\n'}, "newest CHANGELOG entry is 1.2.0"),
    ("(g)", {"docs-LIMITS.md": "# Limits\n"}, "Fading waves"),
    ("(g)", {"CHANGELOG.md": FILES["CHANGELOG.md"].replace(" (see docs-LIMITS, \"Fading waves\")", "")}, "pointer"),
])
def test_each_check_fails_when_its_place_loses_the_name(tmp_path, tag, edits, missing):
    bad = failures(tmp_path, **edits)
    assert any(b.startswith(tag) and missing in b for b in bad), bad


def test_missing_landing_features_list_and_llms_commands_are_skipped(tmp_path):
    out, bad = rc.check(tree(tmp_path, **{"docs/index.html": "<p>none</p>", "llms.txt": "# x\n"}))
    assert bad == [] and sum("skipped" in l for l in out) == 2


def test_main_exit_code(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["x", "--root", str(tree(tmp_path, **{"docs-LIMITS.md": "#\n"}))])
    assert rc.main() == 1 and "docs-LIMITS.md" in capsys.readouterr().out
