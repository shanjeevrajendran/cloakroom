"""False-alarm fixes from the holdout negatives (placeholders, standard numbers, default dates). Each fix has a
leak guard next to it: the real-looking value of the same kind must stay masked. All values are made up."""
import pytest

from cloakroom import engine, rules, safe


@pytest.mark.parametrize("value", ["AC" + "0" * 32, "x" * 12, "ghp_" + "0" * 36, "sk-...", "sk-abc…", "AKIA...XYZ"])
def test_filler_secret_is_safe(value):
    assert safe.reason(value, "SECRET", False)


@pytest.mark.parametrize("value", ["AC" + "3f9a1c" * 5 + "d2", "sk-" + "Zq8vR2mK4pL7wX1n" * 3,
                                   "sk-" + "proj-" + "Ab3dEf9hJk2mNp5qRs8tUv" + "...", "ghp_" + "a1B2" * 9])
def test_real_looking_secret_still_masked(value):
    assert safe.reason(value, "SECRET", False) is None


def test_keyed_filler_still_masked():
    assert safe.reason("0" * 20, "SECRET", True) is None  # a value named by a secret label: only example markers clear it


@pytest.mark.parametrize("value", ["01/01/1970", "1970-01-01", "1900-01-01", "1 January 1970", "Jan 1, 1900"])
def test_default_date_is_safe(value):
    assert safe.reason(value, "DATE", False) == "default placeholder date"


@pytest.mark.parametrize("value", ["01/02/1970", "1971-01-01", "12/01/1970", "1 January 1971"])
def test_other_dates_not_treated_as_default(value):
    assert safe.reason(value, "DATE", False) != "default placeholder date"


def _model_says(monkeypatch, text, value, kind):
    a = text.index(value)
    monkeypatch.setattr(engine, "model_findings", lambda t: [rules.Finding(a, a + len(value), kind, "model:x")])
    monkeypatch.setattr(rules, "scan", lambda t: [])
    return engine.detect(text, mode="full")[0]


@pytest.mark.parametrize("text,value", [("Ranges from RFC 5737 are documentation only.", "5737"),
                                        ("Specimen per ICAO 9303 example.", "9303"),
                                        ("We follow ISO/IEC 27001:2022 controls.", "27001:2022"),
                                        ("Style per PEP 8.", "8")])
def test_standard_number_not_an_account(monkeypatch, text, value):
    assert _model_says(monkeypatch, text, value, "ACCOUNT") == []


@pytest.mark.parametrize("text,value", [("Wire to account 55190023 today.", "55190023"),
                                        ("RFC 5737 aside, our IBAN ref is 4417229011853306.", "4417229011853306"),
                                        ("ISO account 88231-04419", "88231-04419")])
def test_account_numbers_still_masked(monkeypatch, text, value):
    assert len(_model_says(monkeypatch, text, value, "ACCOUNT")) == 1


def test_key_prefix_cut_short_is_safe(monkeypatch):
    assert _model_says(monkeypatch, "OPENAI_API_KEY=sk-...your-key-here...", "sk-", "SECRET") == []


def test_test_mode_key_after_prefix_still_masked(monkeypatch):
    text = "Stripe key sk_test_" + "4eC39HqLyjWDarjtT1zdp7dc"
    assert len(_model_says(monkeypatch, text, "4eC39HqLyjWDarjtT1zdp7dc", "SECRET")) == 1


# a placeholder the agent writes on purpose under a secret key: a description in brackets, a variable, prose
@pytest.mark.parametrize("text", ['{"apiKey": "<LITELLM_API_KEY from .env>"}', "api_key: <your OpenAI key>",
                                  'token = "<paste the token here>"', 'api_key = "${LITELLM_API_KEY}"',
                                  '"apiKey": "your key here"', '"apiKey": "YOUR_API_KEY_HERE"'])
def test_placeholder_under_secret_key_is_safe(text):
    assert engine.detect(text, mode="rules")[0] == []


# leak guards: a real value under the same key, or a real key shape inside the brackets, stays masked
@pytest.mark.parametrize("text,value", [('{"apiKey": "Zq8vR2mK4pL7wX1nAb3dEf9h"}', "Zq8vR2mK4pL7wX1nAb3dEf9h"),
                                        ('{"apiKey": "<sk-' + 'proj-Ab3dEf9hJk2mNp5qRs8tUvWx from .env>"}',
                                         "sk-" + "proj-Ab3dEf9hJk2mNp5qRs8tUvWx"),
                                        ("api_key: <Zq8vR2mK4pL7wX1nAb3dEf9h", "<Zq8vR2mK4pL7wX1nAb3dEf9h")])
def test_real_key_near_placeholder_still_masked(text, value):
    assert [text[f.start:f.end] for f in engine.detect(text, mode="rules")[0]] == [value]
