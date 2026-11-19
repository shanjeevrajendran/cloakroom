"""Lines of the security core that no test ran before 1.5 (found with a coverage run): the fail-closed answers for
odd result shapes, the "safe as it is" rules nobody had pinned, and the gap between two rule findings inside one
model span. Each safe rule has a near miss beside it that must still be masked: an untested rule that clears too
much is a leak."""
import io
import json
import sys

import pytest

from cloakroom import engine, hookentry, rules, safe

MSG = "[stopped]"
KEY = "sk_" + "live_51Hx9QaBcDeFgHiJkLmNoPqRsTuVwXyZ012345"


# --- hookentry: the answers when nothing else can run

def test_withhold_blanks_every_string_in_a_list_and_keeps_its_shape():
    raw = ["first secret", {"text": "second secret", "type": "text", "n": 3}, ["third secret"]]
    out = hookentry._withhold(raw, MSG)
    assert "secret" not in json.dumps(out)
    assert out[0] == MSG and out[1]["type"] == "text" and out[1]["n"] == 3 and isinstance(out[2], list)


def test_mcp_withhold_for_a_plain_string_a_broken_json_string_and_no_result():
    assert hookentry._mcp_withhold("a plain secret line", MSG) == MSG
    assert hookentry._mcp_withhold('[{"type": "text", "text": "cut off secret', MSG) == MSG
    assert hookentry._mcp_withhold(None, MSG) == [{"type": "text", "text": MSG}]


def test_mcp_withhold_keeps_a_json_string_a_json_string():
    raw = json.dumps([{"type": "text", "text": "secret one"}, {"type": "image", "data": "c2VjcmV0"}])
    out = hookentry._mcp_withhold(raw, MSG)
    assert isinstance(out, str) and "secret" not in out and "c2VjcmV0" not in out
    assert [b["type"] for b in json.loads(out)] == ["text", "text"]


def test_a_broken_start_says_so_and_a_broken_scrub_says_nothing():
    assert "not working" in hookentry.closed("start", "ImportError", {})["systemMessage"]
    assert hookentry.closed("scrub", "ImportError", {}) == {}


@pytest.mark.parametrize("stdin", [b"[1, 2]", b'"text"', b"null", b"\xff\xfe"])
def test_hook_input_that_is_not_an_object_withholds_and_denies(stdin, monkeypatch, capsys):
    for event, key in (("post", "updatedToolOutput"), ("pre", "permissionDecision")):
        monkeypatch.setattr(sys, "stdin", type("In", (), {"buffer": io.BytesIO(stdin)})())
        assert hookentry.run(event) == 0
        h = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
        assert key in h and h.get("permissionDecision", "deny") == "deny"


def test_an_agent_answer_that_cannot_be_written_falls_back_to_the_fail_closed_one(monkeypatch, capsys):
    from cloakroom import agents
    real, calls = agents.answer, []

    def breaks_once(agent, event, out, data):
        calls.append(out)
        if len(calls) == 1:
            raise KeyError("a translation bug")
        return real(agent, event, out, data)
    monkeypatch.setattr(agents, "answer", breaks_once)
    data = {"session_id": "s", "cwd": "/tmp", "tool_name": "Bash", "tool_input": {"command": "cat notes"},
            "tool_response": {"stdout": f"STRIPE_KEY={KEY}"}, "hook_event_name": "PostToolUse"}
    monkeypatch.setattr(sys, "stdin", type("In", (), {"buffer": io.BytesIO(json.dumps(data).encode())})())
    hookentry.run("post", agent="codex")
    shown = capsys.readouterr().out
    assert len(calls) == 2 and KEY not in shown and "KeyError" in shown


# --- safe.reason: rules that clear a value, each with a near miss that must stay masked

@pytest.mark.parametrize("value, kind, why, near_miss", [
    (safe.JWT_IO_EXAMPLE + ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c", "SECRET", "jwt.io documentation example",
     "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI5ODc2NTQzMjEwIiwibmFtZSI6IlByaXlhIE5haXIifQ.q8Kd0v3mXw2Lr7tYb1Zc"),
    ("Springfield", "ADDRESS", "public address or place name without a street", "14 Larch Avenue, Springfield"),
    ("1600 Amphitheatre Parkway", "ADDRESS", "public address or place name without a street", "1612 Amphitheatre Lane"),
    ("10.0.0.5", "ADDRESS", "loopback or private IP", "8.8.4.4 Larch Avenue"),
    ("550e8400-e29b-41d4-a716-446655440000", "SECRET", "UUID or hash", "550e8400-e29b-41d4-a716-44665544zzzz"),
    ("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", "SECRET", "UUID or hash",
     "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a0"),
    ("git@github.com:fernhill/billing.git", "URL", "public git remote", "git@git.fernhill.co:fernhill/billing.git"),
    ("dependabot[bot]", "PERSON", "bot account", "Daniel Whitfield"),
    ("28801", "ADDRESS", "bare ZIP code", "14 Larch Avenue 28801"),
])
def test_a_safe_rule_clears_its_value_and_not_the_one_beside_it(value, kind, why, near_miss):
    assert safe.reason(value, kind, False) == why
    assert safe.reason(near_miss, kind, False) is None, near_miss


def test_a_value_under_a_secret_label_is_cleared_only_by_an_example_marker():
    for value in ("550e8400-e29b-41d4-a716-446655440000", "10.0.0.5", "1.4.2"):
        assert safe.reason(value, "SECRET", True) is None  # keyed: password=<this> is masked whatever it looks like
    assert safe.reason("your-api-key-here", "SECRET", True) == "example value"


def test_localhost_and_a_broken_address_are_not_documentation_ranges():
    assert safe._is_internal_ip("localhost:8080") and safe._is_internal_ip("127.0.0.1")
    assert not safe._doc_ips_only("999.999.999.999")


# --- engine: what is left between two rule findings inside one model span

def test_a_number_between_two_rule_findings_in_one_model_span_stays_masked(monkeypatch):
    text = "415-820-4471 ref 7731 529-31-8846"
    monkeypatch.setattr(engine, "model_findings", lambda t: [rules.Finding(0, len(text), "ACCOUNT", "model:account_number")])
    out = engine.mask(text, mode="full").text
    assert "415-820-4471" not in out and "529-31-8846" not in out and "7731" not in out
    assert out.count("PII_") == 3  # the phone, the number between, the SSN: three values, not one blob


def test_words_between_two_rule_findings_in_one_model_span_stay_readable(monkeypatch):
    text = "415-820-4471 SSN 529-31-8846"
    monkeypatch.setattr(engine, "model_findings", lambda t: [rules.Finding(0, len(text), "ACCOUNT", "model:account_number")])
    out = engine.mask(text, mode="full").text
    assert " SSN " in out and out.count("PII_") == 2


def test_a_setting_that_cannot_be_read_masks_the_date(monkeypatch):
    from cloakroom import config

    def broken():
        raise OSError("config.json is unreadable")
    monkeypatch.setattr(config, "load", broken)
    assert engine._personal_date("released on 2026-10-01", 12, 22) is True
    assert engine._own_username("anyone") is False  # and nobody is taken for you


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        engine.detect("text", "fast")
