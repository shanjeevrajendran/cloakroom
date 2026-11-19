"""The session file is written once per mask call, and two processes working on one session never give the same
placeholder to two values. Before 1.5 each new value rewrote the whole file, and two hooks that ran at the same
time (parallel tool calls, rules mode) each gave the next number to a different value: one placeholder, two values,
and the file kept only the last one."""
import os
import subprocess
import sys
from pathlib import Path

from cloakroom import config, engine, store
from cloakroom.vault import Vault


def _path(session="s"):
    return config.vault_path(session)


def test_two_vault_objects_on_one_session_never_share_a_placeholder():
    a, b = Vault(_path()), Vault(_path())  # two hooks, both started before either wrote
    pa = a.placeholder("ana@quillmere.net", "EMAIL")
    pb = b.placeholder("ben@quillmere.net", "EMAIL")
    assert pa != pb
    third = Vault(_path())
    assert third.restore(f"{pa} {pb}") == ("ana@quillmere.net ben@quillmere.net", [])


def test_two_vault_objects_on_an_existing_session_never_share_a_placeholder():
    Vault(_path()).placeholder("first@quillmere.net", "EMAIL")  # the session file is there, with its id
    a, b = Vault(_path()), Vault(_path())
    pa = a.placeholder("ana@quillmere.net", "EMAIL")
    pb = b.placeholder("ben@quillmere.net", "EMAIL")  # before 1.5: the same placeholder as pa, for another value
    assert pa != pb
    assert Vault(_path()).restore(f"{pa} {pb}") == ("ana@quillmere.net ben@quillmere.net", [])


def test_a_value_another_process_masked_keeps_its_placeholder():
    a, b = Vault(_path()), Vault(_path())
    assert a.placeholder("ana@quillmere.net", "EMAIL") == b.placeholder("ana@quillmere.net", "EMAIL")


def test_a_new_session_started_twice_agrees_on_one_vault_id():
    a, b = Vault(_path()), Vault(_path())  # neither found a file
    pa = a.placeholder("ana@quillmere.net", "EMAIL")
    pb = b.placeholder("ben@quillmere.net", "EMAIL")
    assert a.id == b.id == Vault(_path()).id
    assert a.restore(pa)[0] == "ana@quillmere.net" and b.restore(f"{pa} {pb}")[1] == []


def test_mask_writes_the_session_file_once(monkeypatch):
    writes = []
    real = store.write_atomic
    monkeypatch.setattr(store, "write_atomic", lambda path, data, mode=0o600: (writes.append(path), real(path, data, mode)))
    text = "\n".join(f"user{i}@quillmere.net" for i in range(200))
    v = Vault(_path())
    r = engine.mask(text, v, "rules")
    assert len(r.masked) == 200 and len(writes) == 1
    engine.mask(text, v, "rules")  # nothing new: nothing written
    assert len(writes) == 1
    assert len(Vault(_path()).by_placeholder) == 200


def test_placeholders_made_before_a_failure_are_still_saved():
    v = Vault(_path())
    try:
        with v.batch():
            ph = v.placeholder("ana@quillmere.net", "EMAIL")
            raise RuntimeError("the mask call failed half-way")
    except RuntimeError:
        pass
    assert Vault(_path()).restore(ph)[0] == "ana@quillmere.net"


def test_refresh_takes_in_another_process_s_values():
    kept = Vault(_path())  # the service keeps a vault for hours
    ph = Vault(_path()).placeholder("ana@quillmere.net", "EMAIL")  # a hook that ran on the rules alone
    assert kept.restore(ph)[1] == [ph]
    kept.refused.clear()
    kept.refresh()
    assert kept.restore(ph) == ("ana@quillmere.net", [])


def test_no_temporary_file_is_left_behind():
    v = Vault(_path())
    for i in range(5):
        v.placeholder(f"user{i}@quillmere.net", "EMAIL")
    names = sorted(p.name for p in _path().parent.iterdir())
    assert names == ["s.json", "s.lock"]


def test_a_failed_write_leaves_the_old_file_and_no_temporary_file(tmp_path, monkeypatch):
    target = tmp_path / "x.bin"
    store.write_atomic(target, b"old")
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("disk full")))
    try:
        store.write_atomic(target, b"new")
    except OSError:
        pass
    assert target.read_bytes() == b"old" and [p.name for p in tmp_path.iterdir()] == ["x.bin"]


WORKER = """
import sys
from cloakroom import config, engine
from cloakroom.vault import Vault
n = sys.argv[1]
for i in range(40):
    engine.mask(f"user{n}x{i}@quillmere.net and shared@quillmere.net", Vault(config.vault_path("race")), "rules")
"""


def test_four_processes_masking_into_one_session_lose_nothing():
    env = dict(os.environ, PYTHONPATH=str(Path(engine.__file__).parents[1]))
    procs = [subprocess.Popen([sys.executable, "-c", WORKER, str(n)], env=env) for n in range(4)]
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0, 0]
    v = Vault(config.vault_path("race"))
    values = set(v.by_placeholder.values())
    assert len(v.by_placeholder) == len(values) == 4 * 40 + 1  # every value once, under a placeholder of its own
    assert v.counts == {"EMAIL": 161}
