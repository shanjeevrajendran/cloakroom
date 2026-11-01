"""A refused Keychain read is remembered: one get_password call across calls and processes, one message, a restart clears it."""
import importlib
import json
import os

import pytest

from cloakroom import config, crypto, hook


class FakeStore:
    def __init__(self, deny):
        self.deny, self.gets, self.value = deny, 0, None

    def get_password(self, service, account):
        self.gets += 1
        if self.deny:
            raise PermissionError("denied")
        return self.value

    def set_password(self, service, account, value):
        self.value = value


@pytest.fixture
def store(monkeypatch):
    monkeypatch.delenv("CLOAKROOM_VAULT_KEY")
    s = FakeStore(deny=True)
    monkeypatch.setattr(crypto, "_secure_store", lambda: s)
    monkeypatch.setattr(crypto, "_borrowed_home", lambda: False)  # CI may run with a HOME of its own
    return s


def test_denial_asks_once_across_calls_and_processes(store):
    for _ in range(3):
        with pytest.raises(crypto.KeyUnavailable):
            crypto.key()
    importlib.reload(config)  # a second process: no in-memory state, same marker file
    crypto._KEY = None
    with pytest.raises(crypto.KeyUnavailable):
        crypto.key()
    assert store.gets == 1
    marker = config.HOME / "key-denied"
    assert (os.name == "nt" or marker.stat().st_mode & 0o777 == 0o600) and set(  # Windows has no POSIX modes
        json.loads(marker.read_text())) == {"at", "notified"}
    assert not (config.HOME / "vault.key").exists()


def test_message_once_then_restart_clears(store):
    with pytest.raises(crypto.KeyUnavailable):
        crypto.key()
    assert "own rules are off" in hook.on_start({})["systemMessage"]
    assert hook.on_start({}) is None and crypto.denial_notice() is None
    crypto.clear_denial()  # what daemon.serve() does
    store.deny, store.value = False, None
    assert crypto.key() and store.gets == 2


def test_allowed_first_time_one_call_no_message(store):
    store.deny = False
    crypto.key()
    assert store.gets == 1 and crypto.denial_notice() is None and not crypto.denied()


def test_doctor_retry_key_clears_and_retries(store, capsys):
    from cloakroom import cli
    with pytest.raises(crypto.KeyUnavailable):
        crypto.key()
    assert crypto.denied() and store.gets == 1
    cli.main(["doctor", "--retry-key"])  # still denied: asked once more, marker back, the command is named
    out = capsys.readouterr().out
    assert store.gets == 2 and crypto.denied() and "cloakroom doctor --retry-key" in out
    store.deny, store.value = False, None  # the user clicked Always Allow
    cli.main(["doctor", "--retry-key"])
    assert store.gets == 3 and not crypto.denied() and "vault key: OS secure store" in capsys.readouterr().out
