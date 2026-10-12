"""Rules from your labels: suggested first, active only after Approve, Undo puts things back."""
import json

from test_dashboard import call, server, sign_in  # noqa: F401  (server is a fixture)
from test_shell_and_hooks import run_hook


def _mask(text):
    from cloakroom import config, engine
    cfg = config.load()
    return engine.mask(text, mode="rules", allow=cfg["allow"], always=cfg["always"])


def test_pattern_for():
    from cloakroom import userrules
    assert userrules.pattern_for("ACCT-482913") == r"re:ACCT\-[0-9]{6}"
    assert userrules.pattern_for("Grace Hopper") == "Grace Hopper"


def test_suggest_approve_undo_changes_masking():
    from cloakroom import config, userrules
    key = "DB_PASSWORD=Xk9vT2pLq8mN3r"
    assert _mask(key).masked
    [r] = userrules.suggest("e1", "false_alarm", [{"kind": "SECRET", "value": "Xk9vT2pLq8mN3r"}])
    assert r["state"] == "suggested" and r["needs_yes"]
    assert _mask(key).masked  # suggested only: nothing changes
    assert userrules.suggest("e2", "false_alarm", [{"kind": "SECRET", "value": "Xk9vT2pLq8mN3r"}]) == []  # no duplicate
    userrules.decide(r["id"], "approve")
    assert not _mask(key).masked and _mask(key).skipped[0][2] == "on your allowlist"
    assert b"Xk9vT2pLq8mN3r" not in (config.HOME / "rules.bin").read_bytes()
    userrules.decide(r["id"], "undo")
    assert _mask(key).masked and userrules.load() == [] and userrules.decisions() == 1


def test_always_mask_rule_catches_the_shape():
    from cloakroom import userrules
    text = "ticket for ACCT-482913 and ACCT-100200"
    assert not _mask(text).masked
    [r] = userrules.suggest("e1", "missed", [{"kind": "ACCOUNT", "value": "ACCT-482913"}])
    userrules.decide(r["id"], "approve")
    out = _mask(text)
    assert "482913" not in out.text and "100200" not in out.text and out.text.count("PII_ACCOUNT_") == 2
    userrules.drop_suggestions("e1")  # approved rules survive a cleared label
    assert len(userrules.load()) == 1


def test_dashboard_label_suggests_and_approve_applies(server, monkeypatch, capsys):  # noqa: F811
    run_hook("post", {"session_id": "s1", "tool_name": "Read", "tool_input": {"file_path": "/x/.env"},
                      "tool_response": {"file": {"content": "DB_PASSWORD=Xk9vT2pLq8mN3r"}}}, monkeypatch, capsys)
    cookie, _ = sign_in(server)
    w = {**cookie, "X-Cloakroom": "1"}
    ev = json.loads(call(server, "GET", "/api/summary", headers=cookie)[2])["events"][0]
    _, _, raw = call(server, "POST", "/api/label", {"id": ev["id"], "label": "false_alarm", "placeholders": ["PII_SECRET_1"]}, w)
    assert json.loads(raw)["suggested"] == 1
    summary = json.loads(call(server, "GET", "/api/summary", headers=cookie)[2])
    [rule] = summary["rules"]
    assert rule["state"] == "suggested" and rule["type"] == "allow"
    assert call(server, "POST", "/api/rule", {"id": rule["id"], "action": "approve"}, cookie)[0] == 403  # no header
    assert call(server, "POST", "/api/rule", {"id": rule["id"], "action": "approve"}, w)[0] == 200
    assert call(server, "POST", "/api/rule", {"id": rule["id"], "action": "approve"}, w)[0] == 400  # already active
    assert not _mask("DB_PASSWORD=Xk9vT2pLq8mN3r").masked
    summary = json.loads(call(server, "GET", "/api/summary", headers=cookie)[2])
    assert summary["decisions"] == 2 and summary["rules"][0]["state"] == "active"

    _, _, raw = call(server, "POST", "/api/label", {"id": ev["id"], "label": "missed", "missed": [{"kind": "PERSON", "value": "Nadia Petrov"}]}, w)
    summary = json.loads(call(server, "GET", "/api/summary", headers=cookie)[2])
    hidden = [r for r in summary["rules"] if r["type"] == "always_mask"][0]
    assert "Nadia Petrov" not in json.dumps(summary) and hidden["text"].startswith("Na•")
