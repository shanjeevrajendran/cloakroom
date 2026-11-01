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
