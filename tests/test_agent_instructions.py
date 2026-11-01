"""1.2.0: the plugin list an agent sends about itself (Codex: "- Display Name (plugin-id@marketplace)") is not people
and email addresses. A real name with a real address in the same text stays masked. All values are made up."""
import json
import re

import pytest

from cloakroom import engine, proxy, rules

PLUGINS = ("### Available plugins\n- Acme Orbit (acme-orbit@curated-remote)\n- Lumen Calendar (lumen-calendar@curated-remote)\n"
           "- Box (box@curated-remote)\n- Northwind Mail (northwind-mail@curated-remote)\n")
IDS = re.findall(r"[\w-]+@curated-remote", PLUGINS)
PERSON_LINE = "Escalate to Marisol Quenby <marisol.quenby@acme-corp.com> or mail ops.lead@acme-corp.com.\n"


def _body(text):
    return json.dumps({"model": "gpt-x", "input": [{"type": "message", "role": "developer",
                                                    "content": [{"type": "input_text", "text": text}]}]}).encode()


def _model_tags_ids(monkeypatch, text):
    """The model calls each plugin id an email, as it did on a live Codex body."""
    spans = [(m.start(), m.end(), "EMAIL") for m in re.finditer(r"[\w.-]+@[\w-]+(?:\.[\w-]+)*", text)]
    monkeypatch.setattr(engine, "model_findings", lambda t: [rules.Finding(a, b, k, "model:x") for a, b, k in spans])


def test_plugin_list_unmasked_in_rules_mode():
    out, changed, found, _ = proxy.mask_body(_body(PLUGINS))
    assert not changed and found == []


def test_plugin_list_unmasked_in_full_mode(monkeypatch):
    _model_tags_ids(monkeypatch, PLUGINS)
    assert engine.detect(PLUGINS, mode="full")[0] == []


def test_real_name_and_address_next_to_the_plugin_list_still_masked():
    out = json.loads(proxy.mask_body(_body(PLUGINS + PERSON_LINE))[0])["input"][0]["content"][0]["text"]
    for value in ("Marisol Quenby", "marisol.quenby@acme-corp.com", "ops.lead@acme-corp.com"):
        assert value not in out
    assert all(i in out for i in IDS) and "Acme Orbit" in out


def test_real_name_and_address_still_masked_in_full_mode(monkeypatch):
    text = PLUGINS + PERSON_LINE
    _model_tags_ids(monkeypatch, text)
    kept = {f.value(text) for f in engine.detect(text, mode="full")[0]}
    assert {"Marisol Quenby", "marisol.quenby@acme-corp.com", "ops.lead@acme-corp.com"} <= kept and not kept & set(IDS)


def test_a_key_in_the_same_developer_text_still_masked():
    key = "sk-proj-" + "Zq8vT3mW9xK2pL7nR4sY6bH1"
    out = proxy.mask_body(_body(PLUGINS + f"OPENAI_API_KEY={key}\n"))[0].decode()
    assert key not in out


def test_dotless_host_address_is_the_known_limit(monkeypatch):
    """Trade-off on record (docs-LIMITS): an address on a host without a dot is not masked, in either detector."""
    text = "Ask Marisol Quenby <marisol@intranet> about it."
    assert engine.mask(text, mode="rules").text == text
    _model_tags_ids(monkeypatch, text)
    assert engine.detect(text, mode="full")[0] == []


def test_cwd_in_environment_context_is_a_path_not_a_url(monkeypatch):
    """Codex's <cwd>: the model's URL span starts at the tag's '>', which hid the local-path skip."""
    text = "<environment_context>\n  <cwd>/private/tmp/work/demo-app/proj</cwd>\n</environment_context>"
    a = text.index(">/private")
    monkeypatch.setattr(engine, "model_findings", lambda t: [rules.Finding(a, text.index("</cwd>"), "URL", "model:x")])
    assert engine.detect(text, mode="full")[0] == []


def test_username_in_a_tagged_home_path_still_masked(monkeypatch):
    text = "<cwd>/Users/marisolq/code/demo</cwd>"
    a = text.index(">/Users")
    monkeypatch.setattr(engine, "model_findings", lambda t: [rules.Finding(a, text.index("</cwd>"), "URL", "model:x")])
    assert "marisolq" not in engine.mask(text, mode="full").text
