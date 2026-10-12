import json

import pytest

from test_shell_and_hooks import run_hook

SECRET = "Xk9vT2pLq8mN3r"


def test_events_are_value_free(monkeypatch, capsys):
    from cloakroom import config, events
    run_hook("post", {"session_id": "t", "tool_name": "Read", "tool_input": {"file_path": "/x/.env"},
                      "tool_response": {"file": {"content": f"DB_PASSWORD={SECRET}"}}}, monkeypatch, capsys)
    run_hook("pre", {"session_id": "t", "tool_name": "Bash", "tool_input": {"command": f"echo {SECRET} | wc -c"}},
             monkeypatch, capsys)
    raw = (config.HOME / "events.jsonl").read_text()
    assert SECRET not in raw and "echo " not in raw
    newest, first = events.read()
    assert first["tool"] == "Read" and first["target"] == "/x/.env" and first["action"] == "masked"
    assert first["kinds"] == {"SECRET": 1} and first["placeholders"] == ["PII_SECRET_1"]
    assert first["ms"] >= 0 and first["session"] == "t"
    assert newest["target"] == "" and newest["event"] == "pre"  # nothing found: no target logged


def test_event_target_is_masked(monkeypatch, capsys):
    from cloakroom import events
    run_hook("post", {"session_id": "t", "tool_name": "Read", "tool_input": {"file_path": "/x/jane.doe@marrowgate.io/notes.txt"},
                      "tool_response": {"file": {"content": "mail jane.doe@marrowgate.io"}}}, monkeypatch, capsys)
    ev = events.read()[0]
    assert "jane.doe" not in ev["target"] and ev["target"] == "/x/PII_EMAIL_1/notes.txt"
    assert events.target("Bash", {"command": "/usr/bin/grep -rn secret ."}) == "grep"


def test_events_rotate(monkeypatch):
    from cloakroom import config, events
    monkeypatch.setattr(events, "MAX_BYTES", 200)
    ids = [events.record("post", {"tool_name": "Read"}, "pass", [], 1.0)["id"] for _ in range(5)]
    assert (config.HOME / "events.1.jsonl").exists()  # two files at most, so the oldest lines go
    got = [e["id"] for e in events.read()]
    assert got == ids[::-1][:len(got)] and len(got) >= 2 and len(events.read(limit=1)) == 1


def test_labels_encrypted_roundtrip():
    from cloakroom import config, labels
    labels.put("abc", "false_alarm", [{"kind": "ADDRESS", "value": "France"}])
    assert b"France" not in (config.HOME / "labels.bin").read_bytes()
    assert labels.load()["abc"]["items"] == [{"kind": "ADDRESS", "value": "France"}]
    labels.put("abc", "correct")
    assert labels.load()["abc"]["label"] == "correct"
    assert labels.remove("abc") and not labels.remove("abc") and labels.load() == {}
    with pytest.raises(ValueError):
        labels.put("abc", "maybe")
