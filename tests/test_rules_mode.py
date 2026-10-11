"""Fast tests in rules mode (no model needed)."""
from cloakroom import Vault, mask


def test_same_value_same_placeholder():
    v = Vault()
    a = mask("mail jo.smith@fernhill.co", v, mode="rules").text
    b = mask("again jo.smith@fernhill.co", v, mode="rules").text
    assert a.split()[-1] == b.split()[-1] == "PII_EMAIL_1"


def test_restore_round_trip_and_unknown():
    v = Vault()
    masked = mask("DB_PASSWORD=Xk9vT2pLq8mN3r", v, mode="rules").text
    assert "Xk9vT2pLq8mN3r" not in masked
    restored, unknown = v.restore(masked + " PII_EMAIL_7")
    assert "Xk9vT2pLq8mN3r" in restored and unknown == ["PII_EMAIL_7"]


def test_placeholders_are_shell_safe():
    v = Vault()
    out = mask("card 4539 1488 0343 6467", v, mode="rules").text
    ph = out.split()[-1]
    assert ph.replace("_", "").isalnum()


def test_known_safe_values_untouched():
    text = "AKIAIOSFODNN7EXAMPLE alice@example.com 4242 4242 4242 4242 127.0.0.1 os.environ[\"DB_PASSWORD\"]"
    assert mask(text, mode="rules").text == text


def test_own_placeholders_not_remasked():
    assert mask("restored PII_EMAIL_1 and PII_PERSON_2", mode="rules").changed is False


def test_home_dir_usernames_masked_generic_ones_kept():
    for text, name in [('File "/Users/maria.garcia/app/w.py"', "maria.garcia"), ("cwd: /home/okafor/sync", "okafor"),
                       ("open C:\\Users\\李伟\\AppData\\x", "李伟"), ('"C:\\\\Users\\\\jdoe\\\\x"', "jdoe")]:
        assert name not in mask(text, mode="rules").text
    for text in ["/home/runner/work", "C:\\Users\\Public\\x", "/Users/$USER/x", "/Users/<you>/x"]:
        assert mask(text, mode="rules").text == text


def test_url_username_and_its_repeats_masked():
    out = mask("https://lee.park:pw12345@git.x.io/lee.park/etl.git", mode="rules").text
    assert "lee.park" not in out and "pw12345" not in out
    assert "postgres://postgres:" in mask("postgres://postgres:s3cret99@db/x", mode="rules").text


def test_known_safe_values_left_alone():
    from cloakroom import safe
    assert safe.reason("[REDACTED]", "SECRET", True) == "already redacted"
    assert safe.reason("sk-test-4eC39HqLyjWDarjtT1zdp7dc", "SECRET", False) == "test-mode key"
    assert safe.reason("9999 9999 9999", "PHONE", False) == "repeated-digit placeholder"
    assert safe.reason("4242 4242 4242 4242", "ACCOUNT", False) == "published test card"
    assert safe.reason("QQ 12 34 56 C", "ACCOUNT", False) == "HMRC test NI number"
    assert safe.reason("John Doe", "PERSON", False) == "placeholder name"
    assert safe.reason("14 March 1879", "DATE", False) and safe.reason("8am", "DATE", False)
    assert safe.reason("1-800-555-1212", "PHONE", False)
    assert safe.reason("http://127.0.0.1:8080/healthz", "URL", False)


def test_real_looking_values_still_masked():
    from cloakroom import safe
    assert safe.reason("(555) 234-7781", "PHONE", False) is None  # 555 stand-ins for real numbers stay masked
    assert safe.reason("4539 1488 0343 6467", "ACCOUNT", False) is None
    assert safe.reason("Maria Okafor", "PERSON", False) is None
    assert safe.reason("12 May 1987", "DATE", False) is None
    assert safe.reason("221 Baker Street, London", "ADDRESS", False) is None


def test_false_alarm_round_two():
    from cloakroom import safe
    assert safe.reason("***MASKED***", "SECRET", True) == "already redacted"
    assert safe.reason("02:00-02:30", "DATE", False) and safe.reason("02:00 UTC", "DATE", False)
    assert safe.reason("museum", "DATE", False) == "not a date"
    assert safe.reason("15 April", "DATE", False) == "date without a year"
    assert safe.reason("6 July 1907", "DATE", False)
    assert safe.reason("0123456789", "ACCOUNT", False) == "sequential-digit placeholder"
    assert safe.reason("203.0.113.7, 198.51.100.23", "ADDRESS", False) == "documentation IP range"
    assert safe.reason("AIzaSy...", "SECRET", False) == "truncated or prefix-only key"
    assert safe.reason("Nikola Tesla", "PERSON", False) == "historical or public figure"


def test_false_alarm_round_two_keeps_real_values():
    from cloakroom import safe
    assert safe.reason("12 May 1987", "DATE", False) is None
    assert safe.reason("4539148803436467", "ACCOUNT", False) is None
    assert safe.reason("34.120.18.7", "ADDRESS", False) is None
    assert safe.reason("AIzaSyD8kQ2mX7vN4pL9wR3tY6uB1cF5hJ0gE2a", "SECRET", False) is None
    assert safe.reason("Nikola Petrovic", "PERSON", False) is None


def test_v4_leak_classes():
    from cloakroom import safe
    assert safe.reason("34.120.18.7:9100", "ADDRESS", False) is None  # public IP:port is not a code location
    assert safe.reason("app.py:42", "URL", False) == "code location"
    out = mask('198.51.100.4 - mkowalski [10/Oct/2026:13:55:36 +0000] "GET / HTTP/1.1" 200', mode="rules").text
    assert "mkowalski" not in out
    assert mask('127.0.0.1 - - [10/Oct/2026:13:55:36 +0000] "GET /"', mode="rules").text.count("PII_") == 0
    out = mask('# aws_iam_access_key.mira will be created\n  user = "mira"', mode="rules").text
    assert "mira" not in out
    assert "PII_SECRET" in mask("TWILIO_SID=AC0123456789abcdef0123456789abcdef", mode="rules").text
    assert mask("Authorization: Bearer SG.XXXX.YYYY", mode="rules").text.count("PII_") <= 1


def test_false_alarm_round_three_safe_parts():
    from cloakroom import safe
    assert safe.reason("$ANTHROPIC_API_KEY", "SECRET", True) == "reference to a variable, not a value"
    assert safe.reason("${DB_PASSWORD}", "SECRET", True)
    assert safe.reason("(800) 123-4567", "PHONE", False) == "toll-free or test number"
    assert safe.reason("+15005550006", "PHONE", False) == "toll-free or test number"
    assert safe.reason("999888777666", "ACCOUNT", False) == "repeated-group placeholder"
    assert safe.reason("7aa2f1c0-4e55-4a1d-9c2f-0e8d7c", "ACCOUNT", False) == "UUID or hash fragment"
    assert safe.reason("30 3", "DATE", False) == "not a full date"
    assert mask("Authorization: Bearer SG.XXXX.YYYY", mode="rules").text.count("PII_") == 0
    assert "aZ81kd02LmQ7xPv3" not in mask("Authorization: Bearer aZ81kd02LmQ7xPv3", mode="rules").text
    # still masked
    assert safe.reason("(415) 382-9147", "PHONE", False) is None
    assert safe.reason("4539148803436467", "ACCOUNT", False) is None


def test_final_round_v5_classes():
    from cloakroom import safe
    fp = "SHA256:" + "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5zA7bC9d"
    assert "PII_SECRET" in mask("key fingerprint " + fp, mode="rules").text
    assert "obennett" not in mask("sshd[1]: Accepted publickey for obennett from 34.1.2.3", mode="rules").text
    assert "mlopez" not in mask("database: search-api user mlopez", mode="rules").text
    assert mask("host=db1 user=root", mode="rules").text == "host=db1 user=root"
    assert mask("the user is ready on the host", mode="rules").text == "the user is ready on the host"
    assert safe.reason("SSWS 00AbCdEfGhIjKlMnOpQrStUvW", "SECRET", False)
    assert safe.reason("123456", "SECRET", False) and safe.reason("123456", "SECRET", True) is None
    assert safe.reason("arn", "PERSON", False) and safe.reason("Bo Li", "PERSON", False) is None


def test_auto_backend_survives_a_broken_torch(monkeypatch):
    import builtins
    from cloakroom import engine
    real_import = builtins.__import__

    def broken(name, *a, **k):
        if name == "torch":
            raise OSError("[WinError 126] c10.dll")  # torch installed but its DLLs can't load
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", broken)
    monkeypatch.setenv("CLOAKROOM_BACKEND", "auto")
    assert engine.choose_backend() in ("onnx", "torch")  # no exception


def test_cli_mask_handles_bom_and_non_ascii_on_a_legacy_console(tmp_path):
    import os, subprocess, sys
    f = tmp_path / "config.py"
    f.write_bytes("﻿OWNER = \"李伟\"\nADMIN_EMAIL = \"henrik.dahl@quillfield.com\"\n".encode("utf-8"))
    env = dict(os.environ, PYTHONIOENCODING="cp1252", CLOAKROOM_MODE="rules", CLOAKROOM_HOME=str(tmp_path / "h"),
               PYTHONPATH=os.path.join(os.path.dirname(__file__), "..", "src"))
    out = subprocess.run([sys.executable, "-m", "cloakroom", "mask", str(f)], env=env, capture_output=True)
    assert out.returncode == 0, out.stderr.decode(errors="replace")
    text = out.stdout.decode("utf-8")
    assert "henrik.dahl@quillfield.com" not in text and not text.startswith("﻿")
