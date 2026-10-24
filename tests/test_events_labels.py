import json

import pytest

from test_shell_and_hooks import run_hook
from conftest import untag

SECRET = "Xk9vT2pLq8mN3r"


def test_events_are_value_free(monkeypatch, capsys):
    from cloakroom import config, events
    run_hook("post", {"session_id": "t", "tool_name": "Read", "tool_input": {"file_path": "/x/.env"},
                      "tool_response": {"file": {"content": f"DB_PASSWORD={SECRET}"}}}, monkeypatch, capsys)
    run_hook("pre", {"session_id": "t", "tool_name": "Bash", "tool_input": {"command": f"echo {SECRET} | wc -c"}},
             monkeypatch, capsys)
    raw = "".join(p.read_text() for p in events.months())
    assert SECRET not in raw and "echo " not in raw
    newest, first = events.read()
    assert first["tool"] == "Read" and first["target"] == "/x/.env" and first["action"] == "masked"
    assert first["kinds"] == {"SECRET": 1} and [untag(p) for p in first["placeholders"]] == ["PII_SECRET_1"]
    assert first["ms"] >= 0 and first["session"] == "t"
    assert newest["target"] == "" and newest["event"] == "pre"  # nothing found: no target logged


def test_event_target_is_masked(monkeypatch, capsys):
    from cloakroom import events
    run_hook("post", {"session_id": "t", "tool_name": "Read", "tool_input": {"file_path": "/x/jane.doe@marrowgate.io/notes.txt"},
                      "tool_response": {"file": {"content": "mail jane.doe@marrowgate.io"}}}, monkeypatch, capsys)
    ev = events.read()[0]
    assert "jane.doe" not in ev["target"] and untag(ev["target"]) == "/x/PII_EMAIL_1/notes.txt"
    assert events.target("Bash", {"command": "/usr/bin/grep -rn secret ."}) == "grep"


def test_events_monthly_files_and_legacy_still_read():
    from cloakroom import config, events
    config.private_dir()
    (config.HOME / "events.1.jsonl").write_text(json.dumps({"id": "old1", "ts": "2026-01-01T00:00:00"}) + "\n")
    (config.HOME / "events.jsonl").write_text(json.dumps({"id": "old2", "ts": "2026-02-01T00:00:00"}) + "\n")
    ids = [events.record("post", {"tool_name": "Read"}, "pass", [], 1.0)["id"] for _ in range(3)]
    assert [e["id"] for e in events.read()] == ids[::-1] + ["old2", "old1"]
    assert len(events.months()) == 1 and events.verify()["ok"] and events.verify()["lines"] == 3
    assert json.loads((config.HOME / "events.jsonl").read_text())["id"] == "old2"  # never rewritten


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
