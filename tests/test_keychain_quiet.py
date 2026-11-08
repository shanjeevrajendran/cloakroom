"""macOS: the vault key lives in an item /usr/bin/security creates and reads (account vault-key-v2), so no
"python wants to use your Keychain" dialog on first run or after a Python update. A 1.3 item (vault-key, made by
python through keyring) is copied once and left as it was. Windows and Linux keep the keyring path. A fake
/usr/bin/security stands in: no test touches the real Keychain."""
import base64

import pytest

from cloakroom import config, crypto

OLD = base64.b64encode(b"o" * 32).decode()


class FakeKeyring:
    """The 1.3 keyring item (service cloakroom, account vault-key)."""
    def __init__(self, value=None, deny=False):
        self.value, self.deny, self.gets, self.sets = value, deny, 0, 0

    def get_password(self, service, account):
        assert (service, account) == ("cloakroom", "vault-key")
        self.gets += 1
        if self.deny:
            raise PermissionError("denied")
        return self.value

    def set_password(self, service, account, value):
        self.sets += 1
        self.value = value


class FakeSecurity:
    """/usr/bin/security: items by (service, account), the argv of every run, and what came on stdin."""
    def __init__(self, deny=False, broken_add=False):
        self.items, self.calls, self.deny, self.broken_add = {}, [], deny, broken_add

    def __call__(self, args, stdin=None):
        self.calls.append((list(args), stdin))
        if args[0] == "find-generic-password":
            if self.deny:
                return 51, ""
            v = self.items.get((args[args.index("-s") + 1], args[args.index("-a") + 1]))
            return (0, v + "\n") if v else (crypto.NOT_FOUND, "")
        if args == ["-i"]:
            words = stdin.split()
            assert words[0] == "add-generic-password" and "-T" in words and words[words.index("-T") + 1] == crypto.SECURITY
            if not self.broken_add:
                self.items.setdefault((words[words.index("-s") + 1], words[words.index("-a") + 1]),
                                      words[words.index("-w") + 1])
            return 0, ""
        raise AssertionError(f"unexpected security call {args}")


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.delenv("CLOAKROOM_VAULT_KEY")
    monkeypatch.setattr(crypto, "_borrowed_home", lambda: False)
    monkeypatch.setattr(crypto, "_mac_tool", lambda: True)
    sec, kr = FakeSecurity(), FakeKeyring()
    monkeypatch.setattr(crypto, "_security", sec)
    monkeypatch.setattr(crypto, "_secure_store", lambda: kr)
    return sec, kr


def _fresh_process():
    crypto._KEY = None


def test_first_run_creates_v2_through_security_and_never_python(mac):
    sec, kr = mac
    k = crypto.key()
    assert len(k) == 32 and crypto.KEY_SOURCE == "os-secure-store"
    assert base64.b64decode(sec.items[("cloakroom", "vault-key-v2")]) == k
    assert kr.sets == 0  # python never creates an item on macOS any more


def test_key_never_on_a_command_line(mac):
    sec, _ = mac
    crypto.key()
    stored = sec.items[("cloakroom", "vault-key-v2")]
    assert all(stored not in " ".join(args) for args, _ in sec.calls)
    assert any(stdin and stored in stdin for _, stdin in sec.calls)  # it went on stdin


def test_later_runs_read_v2_only(mac):
    sec, kr = mac
    first = crypto.key()
    _fresh_process()
    gets = kr.gets
    assert crypto.key() == first and kr.gets == gets  # the python-made item isn't asked again


def test_a_13_key_is_copied_once_and_the_old_item_is_left_alone(mac):
    sec, kr = mac
    kr.value = OLD
    assert crypto.key() == base64.b64decode(OLD)
    assert sec.items[("cloakroom", "vault-key-v2")] == OLD and kr.value == OLD and kr.sets == 0
    _fresh_process()
    assert crypto.key() == base64.b64decode(OLD) and kr.gets == 1


def test_another_process_won_the_race(mac):
    """Two processes create v2 at once: both use what the item holds, so nothing is sealed with a lost key."""
    sec, _ = mac
    theirs = base64.b64encode(b"t" * 32).decode()
    real = sec.__call__

    def racing(args, stdin=None):
        if args == ["-i"]:
            sec.items.setdefault(("cloakroom", "vault-key-v2"), theirs)
        return real(args, stdin)
    crypto._security = racing
    assert crypto.key() == base64.b64decode(theirs)


def test_refused_old_item_never_mints_a_new_key(mac):
    sec, kr = mac
    kr.deny = True
    with pytest.raises(crypto.KeyUnavailable):
        crypto.key()
    assert ("cloakroom", "vault-key-v2") not in sec.items and not (config.HOME / "vault.key").exists()
    assert crypto.denied()


def test_refused_v2_read_is_remembered_and_never_mints(mac):
    sec, kr = mac
    sec.deny = True
    for _ in range(2):
        with pytest.raises(crypto.KeyUnavailable):
            crypto.key()
    assert len([c for c in sec.calls if c[0][0] == "find-generic-password"]) == 1  # the marker stops a second ask
    assert kr.gets == 0 and not sec.items and not (config.HOME / "vault.key").exists()


def test_failed_create_is_a_refusal_not_a_key_file(mac):
    sec, _ = mac
    sec.broken_add = True
    with pytest.raises(crypto.KeyUnavailable):
        crypto.key()
    assert not (config.HOME / "vault.key").exists()


def test_windows_and_linux_keep_the_keyring_item(mac, monkeypatch):
    sec, kr = mac
    monkeypatch.setattr(crypto, "_mac_tool", lambda: False)
    k = crypto.key()
    assert base64.b64decode(kr.value) == k and kr.sets == 1 and not sec.calls


def test_borrowed_home_still_skips_every_store(mac, monkeypatch):
    sec, kr = mac
    monkeypatch.setattr(crypto, "_borrowed_home", lambda: True)
    crypto.key()
    assert crypto.KEY_SOURCE == "key-file" and not sec.calls and kr.gets == 0


def test_mac_tool_only_on_macos(monkeypatch):
    monkeypatch.undo()  # the real check, not conftest's stand-in
    monkeypatch.setattr(crypto.sys, "platform", "linux")
    assert crypto._mac_tool() is False
