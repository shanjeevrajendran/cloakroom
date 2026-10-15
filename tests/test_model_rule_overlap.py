"""A model span that overlaps the rules' findings must not hide words or flip a safe value to masked. A fake model
returns the spans PII-Tracer gave on the MacBook Air on 2026-10-15, so these run without weights."""
from cloakroom import Vault, engine, rules


def _fake_model(spans):
    def model_findings(text):
        out = []
        for a, b, label in spans:
            for s, e, k in engine._pieces(text, a, b, engine.MODEL_KIND[label]):
                out.append(rules.Finding(s, e, k, f"model:{label}"))
        return out
    return model_findings


def test_model_span_across_phone_and_ssn_keeps_rule_labels(monkeypatch):
    # PII-Tracer returns one account_number span 12-40 over "415-820-4471 SSN 529-31-8846"
    text = "Priya Raman 415-820-4471 SSN 529-31-8846\n"
    monkeypatch.setattr(engine, "model_findings", _fake_model([(0, 11, "private_person"), (12, 40, "account_number")]))
    v = Vault()
    r = engine.mask(text, vault=v, mode="full")
    assert r.text == "PII_PERSON_1 PII_PHONE_1 SSN PII_SSN_1\n"
    assert v.restore(r.text)[0] == text


def test_fictional_phone_labelled_account_by_model_is_skipped(monkeypatch):
    text = "Jane Doe 415-555-0134 SSN 123-45-6789\n"
    monkeypatch.setattr(engine, "model_findings", _fake_model(
        [(0, 8, "private_person"), (9, 21, "account_number"), (26, 37, "account_number")]))
    assert engine.mask(text, vault=Vault(), mode="full").text == text
