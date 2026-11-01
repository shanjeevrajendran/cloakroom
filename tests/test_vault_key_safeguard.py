"""A Cloakroom process never reaches the OS secure store from a borrowed HOME (a test, a kit), and every script that
sets its own CLOAKROOM_HOME also sets its own vault key; Cursor's wrapper carries the data folder and port, never a key."""
import base64
import os
import pathlib
import re
import shlex

import pytest

from cloakroom import config, crypto

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Boom:
    def get_password(self, *a):
        raise AssertionError("the OS secure store was reached")

    set_password = get_password


@pytest.mark.skipif(os.name == "nt", reason="the HOME check is POSIX only")
def test_borrowed_home_uses_the_key_file_not_the_store(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOAKROOM_VAULT_KEY")
    monkeypatch.setenv("HOME", str(tmp_path / "other-home"))
    monkeypatch.setattr(crypto, "_secure_store", lambda: Boom())
    k = crypto.key()
    assert crypto.KEY_SOURCE == "key-file" and len(k) == 32
    assert base64.b64decode((config.HOME / "vault.key").read_text()) == k


def test_environment_key_still_wins_on_a_borrowed_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "other-home"))
    monkeypatch.setattr(crypto, "_secure_store", lambda: Boom())
    crypto.key()
    assert crypto.KEY_SOURCE == "environment"


def test_scripts_that_set_a_data_folder_set_a_vault_key():
    scripts = [p for d in ("bench", "scripts") for p in ROOT.glob(f"{d}/**/*.sh")]
    setters = [p for p in scripts if "CLOAKROOM_HOME" in p.read_text()]
    assert {p.name for p in setters} >= {"live_test.sh", "live_cursor.sh", "run.sh"}
    assert [p.name for p in setters if "CLOAKROOM_VAULT_KEY" not in p.read_text()] == []


def test_cursor_wrapper_carries_home_and_port_but_no_key(monkeypatch, tmp_path):
    from cloakroom import agents, cursor
    monkeypatch.setattr(agents, "_program", lambda: "/usr/local/bin/cloakroom")
    monkeypatch.setenv("CLOAKROOM_PORT", "8797")
    monkeypatch.setenv("CLOAKROOM_VAULT_KEY", base64.b64encode(b"s" * 32).decode())
    cmd = cursor.wrap({"tool_input": {"command": "ls"}, "cwd": str(tmp_path)}, None)["hookSpecificOutput"][
        "updatedInput"]["command"]
    assert re.fullmatch(r"env CLOAKROOM_HOME=\S+ CLOAKROOM_PORT=8797 /usr/local/bin/cloakroom cursor-run \S{16} ls", cmd)
    assert shlex.split(cmd)[1] == f"CLOAKROOM_HOME={config.HOME}" and "VAULT_KEY" not in cmd and "c3Nz" not in cmd
    assert cursor.gate({"command": cmd}) is None
