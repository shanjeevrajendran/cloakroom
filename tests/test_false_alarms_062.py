"""0.6.2 false-alarm levers (bench/false-alarms-0.6.2/levers.md). Each look-alike that must pass has a guard next to
it: the real-looking secret of the same shape must stay masked. All values are made up."""
import pytest

from cloakroom import engine, rules, safe


def _model_says(monkeypatch, text, value, kind):
    a = text.index(value)
    monkeypatch.setattr(engine, "model_findings", lambda t: [rules.Finding(a, a + len(value), kind, "model:x")])
    monkeypatch.setattr(rules, "scan", lambda t: [])
    return engine.detect(text, mode="full")[0]


def _masked(text, value):
    return value not in engine.mask(text, mode="rules").text


# 1. versions, code constants, GPG key ids
@pytest.mark.parametrize("text", ["minipass-flush: ^1.0.5", "token-types: ~4.2.1"])
def test_version_range_under_key_is_safe(text):
    assert not _masked(text, text.split(": ")[1])


@pytest.mark.parametrize("value", ["0.76.8", "v2.3.11", "1.2.3-beta.1"])
def test_version_is_safe(value):
    assert safe.reason(value, "ACCOUNT", False) == "version number"


@pytest.mark.parametrize("value", ["12.05.1984", "1.2.3.4.5", "2.0.1999"])
def test_dates_and_long_dotted_not_versions(value):
    assert safe.reason(value, "ACCOUNT", False) != "version number"


@pytest.mark.parametrize("text,value", [("SYS_FUTEX_WAIT = 5323", "5323"), ("MAX_RETRY_COUNT: 120", "120"),
                                        ("CENTOS_GPG_KEY_ID = 'C105B9DE'", "C105B9DE")])
def test_constant_or_gpg_id_not_an_account(monkeypatch, text, value):
    assert _model_says(monkeypatch, text, value, "ACCOUNT") == []


@pytest.mark.parametrize("text,value", [("ADMIN_PIN = 4821", "4821"), ("ADMINPIN=4821", "4821"),
                                        ("APIKEY=482193", "482193"), ("DB_APIKEY = 482193", "482193"),
                                        ("SECRET_KEY = 123456", "123456"), ("SYS_PKEY_FREE = 5325", "5325"),
                                        ("BUILD_ID = 1234567", "1234567"), ("GPG_KEY_ID = 'C105B9DE77'", "C105B9DE77")])
def test_secret_constants_still_masked(monkeypatch, text, value):
    assert len(_model_says(monkeypatch, text, value, "ACCOUNT")) == 1


# 2. UI strings under a label or message key
@pytest.mark.parametrize("text,value", [('"incorrectPassword": "Wrong user name or password",', "Wrong user name"),
                                        ("'Reset Password' => 'Ailosod Cyfrinair',", "Ailosod Cyfrinair"),
                                        ('"passwordRequired": "$t(lockRoomPasswordUppercase) deviga"', "deviga"),
                                        ('"tokenAuthFailedTitle": "Authentifizierung fehlgeschlagen"', "fehlgeschlagen")])
def test_ui_string_is_safe(monkeypatch, text, value):
    assert _model_says(monkeypatch, text, value, "SECRET") == []
    assert not _masked(text, value)


@pytest.mark.parametrize("text,value", [('password: "correct horse battery staple"', "correct horse battery staple"),
                                        ('"password": "correct horse battery staple"', "correct horse battery staple"),
                                        ("ADMIN_PASSWORD=Summer Rain 2026", "Summer")])
def test_passwords_in_words_still_masked(text, value):
    assert _masked(text, value)


@pytest.mark.parametrize("text,value", [('password: "correct horse battery staple"', "correct horse battery staple"),
                                        ("ADMIN_PASSWORD=Summer Rain 2026", "Summer Rain 2026"),
                                        ('"passwordHint": "Rain 2026 river"', "Rain 2026 river"),
                                        ("'Reset Password' => 'Hunter2rain'", "Hunter2rain"),
                                        ('"newPassword": "Lantern Copper Field"', "Lantern Copper Field")])
def test_model_secret_in_words_still_masked(monkeypatch, text, value):
    assert len(_model_says(monkeypatch, text, value, "SECRET")) == 1


# 3. variable references under a password key
@pytest.mark.parametrize("text", ["password: req.body.password,", "password: user.password", "Password: c.Redis.Password"])
def test_password_variable_reference_is_safe(text):
    assert engine.mask(text, mode="rules").text == text


@pytest.mark.parametrize("text,value", [("password: hunter2Password99", "hunter2Password99"),
                                        ("pwd=secretpass", "secretpass"), ('password: "user.password"', "user.password")])
def test_password_literal_still_masked(text, value):
    assert _masked(text, value)


@pytest.mark.parametrize("text,value", [
    ('"max_output_tokens": 128000', "128000"),
    ("max_output_tokens: 128000", "128000"),
    ("MAX_TOKENS: 128000", "128000"),
    ("passenger_count: 1234567", "1234567"),
])
def test_token_substring_in_a_parameter_name_is_safe(text, value):
    """keyed-secret used to fire on 'token' inside max_output_tokens (Codex tool descriptions)."""
    assert not _masked(text, value)


@pytest.mark.parametrize("text,value", [
    ("token = Zq8vT3mW9xK2pL7n", "Zq8vT3mW9xK2pL7n"),
    ("auth_token: Zq8vT3mW9xK2pL7n", "Zq8vT3mW9xK2pL7n"),
    ("authtoken=Zq8vT3mW9xK2pL7n", "Zq8vT3mW9xK2pL7n"),
    ("api_key=hunter2Password99", "hunter2Password99"),
    ("secretkey=hunter2Password99", "hunter2Password99"),
    ('"accessToken": "Zq8vT3mW9xK2pL7n"', "Zq8vT3mW9xK2pL7n"),
])
def test_real_secret_keys_still_masked_when_token_is_a_segment(text, value):
    assert _masked(text, value)


# 4. AWS documentation ids
@pytest.mark.parametrize("value", ["111122223333", "arn:aws:kms:us-west-2:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab",
                                   "arn:aws:iam::123456789012:role/DeployRole", "11111111-1111-1111-1111-111111111111"])
def test_aws_doc_example_is_safe(value):
    assert safe.reason(value, "SECRET", False) == "AWS documentation example id"


@pytest.mark.parametrize("value", ["arn:aws:kms:us-east-2:111122223333:key/4e322045-072c-7a42-e711-cef893ea681d",
                                   "arn:aws:kms:us-east-2:840219637751:key/1234abcd-12ab-34cd-56ef-1234567890aa",
                                   "elasticbeanstalk-us-west-2-0123456789012", "123456789012x7Qp"])
def test_real_aws_ids_still_masked(value):
    assert safe.reason(value, "SECRET", False) != "AWS documentation example id"


# 5. XAML resource names
def test_xaml_resource_name_not_a_person(monkeypatch):
    assert _model_says(monkeypatch, '<Color x:Key="McFanning">#42526E</Color>', "McFanning", "PERSON") == []


def test_person_in_xaml_text_still_masked(monkeypatch):
    text = '<TextBlock Text="Signed by Marisol Quenby" />'
    assert len(_model_says(monkeypatch, text, "Marisol Quenby", "PERSON")) == 1


# 6. ChangeLog file entries
@pytest.mark.parametrize("text", ["        * platform/gtk/fast/forms/password-placeholder-expected.png: Added.",
                                  "* plugins/pass-different-npp-struct.html: Copied from plugins/x.html"])
def test_changelog_entry_is_safe(text):
    assert engine.mask(text, mode="rules").text == text


@pytest.mark.parametrize("text,value", [("* notes: password: Hunter2Rain99", "Hunter2Rain99"),
                                        ("password: Added.x9Kq", "Added.x9Kq")])
def test_password_near_changelog_shape_still_masked(text, value):
    assert _masked(text, value)
