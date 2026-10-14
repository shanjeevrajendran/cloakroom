import re
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


def test_own_username_in_a_path_is_left_alone(monkeypatch):
    import getpass
    from cloakroom import engine
    monkeypatch.setattr(getpass, "getuser", lambda: "shan")
    r = engine.mask("Read /Users/shan/notes.md and /home/alice/x.txt", mode="rules")
    assert "/Users/shan/" in r.text and "alice" not in r.text and r.masked[0][1] == "USERNAME"
    assert any("your own username" in why for _, _, why in r.skipped)


def test_runaway_model_span_is_split_back_into_rows():
    from cloakroom import engine
    text = "name,phone,city\nNadia Petrov,+1 415 820 4471,Oakland\nCormac Doyle,+44 7700 183 562,Leeds\n"
    start = text.index(",+1")
    end = text.index("Cormac Doyle") + len("Cormac Doyle")
    pieces = [(text[a:b], k) for a, b, k in engine._pieces(text, start, end, "PERSON")]
    # the city goes; the phone stays masked under its own kind; the line break no longer joins rows
    assert pieces == [("+1 415 820 4471", "PHONE"), ("Cormac Doyle", "PERSON")]
    email = "a,lena.okafor@brightfield.co,+1 312 820 0147,Chicago"
    s = email.index(",lena")
    assert [(email[a:b], k) for a, b, k in engine._pieces(email, s, len(email), "EMAIL")] == [
        ("lena.okafor@brightfield.co", "EMAIL"), ("+1 312 820 0147", "PHONE")]


def test_model_span_widens_to_whole_words():
    from cloakroom import engine
    text = "2026-10-11 09:31 host key accepted"
    assert [text[a:b] for a, b, _ in engine._pieces(text, 1, 16, "DATE")] == ["2026-10-11 09:31"]
    name = "Smith, John called"
    assert [name[a:b] for a, b, _ in engine._pieces(name, 0, 11, "PERSON")] == ["Smith, John"]  # fits a name: kept whole


def test_address_field_values_are_masked():
    from cloakroom import engine
    for text, value in [('{"street": "Branston Lane", "city": "Grantham"}', "Branston Lane"),
                        ("- Street: Southeast Linn Street\n- City: Portland", "Southeast Linn Street"),
                        ("Home address: 18 Larkspur Close, Didsbury\n", "18 Larkspur Close, Didsbury")]:
        assert value not in engine.mask(text, mode="rules").text, text
    for text in ["Email Address: help desk", "see https://maps.example/?address=Paris&key=1",
                 "located at 123 Main Street, Anytown, DE 19999"]:  # no key, so the street rule handles it whole
        r = engine.mask(text, mode="rules")
        assert not any(src == "address-field" for _, _, src in r.masked), text


def test_birth_dates_in_more_formats():
    from cloakroom import engine
    for text in ["<dob>1994-06-18T00:00:00</dob>", "Date of Birth: 1965-09-12T00:00:00", "**DOB:** 03.04.1988",
                 "born on August 10th, 1974", "Birthday: 10th December 1990"]:
        assert "PII_DATE_1" in engine.mask(text, mode="rules").text, text
    assert "1815" in engine.mask("Ada Lovelace was born 10 December 1815.", mode="rules").text  # historical stays


def test_short_passwords_under_keys():
    from cloakroom import engine
    for text, value in [("**Password:** ^{zHw6X-o4'", "zHw6X"), ('"passcode": "g!V9,",', "g!V9"),
                        ("PIN: 4471", "4471")]:  # (a value with <...> reads as a template, "<your password>": left)
        assert value not in engine.mask(text, mode="rules").text, text


def test_account_field_tags(monkeypatch):
    from cloakroom import engine
    text = ":35E:1.23456789\n77S\n:63A:Tabea V. Thies\nmember MBR-ALPLCBLKIW"
    start = text.index("1.23")
    pieces = [(text[a:b], k) for a, b, k in engine._pieces(text, start - 5, len(text), "ACCOUNT")]
    values = [v for v, _ in pieces]
    assert "1.23456789" in values and ("Tabea V. Thies", "PERSON") in pieces  # tag trimmed, name relabelled
    assert ("member MBR-ALPLCBLKIW", "ACCOUNT") in pieces and "77S" in values  # pieces of a split keep their mask
    whole = [(text[a:b], k) for a, b, k in engine._pieces("code 77S here", 5, 8, "ACCOUNT")]
    assert whole == []  # a bare field tag on its own is not an account
    assert engine._not_account("DDA") and not engine._not_account("MBR-ALPLCBLKIW")
    assert [x for x, _ in [(t[a:b], k) for t in ["Doors open 18:00"] for a, b, k in engine._pieces(t, 11, 16, "DATE")]] == ["18:00"]


def test_dates_masked_only_in_personal_context(monkeypatch):
    from cloakroom import engine, rules

    def fake_model(text):  # every date the model would flag
        import re
        return [rules.Finding(m.start(), m.end(), "DATE", "model:private_date")
                for m in re.finditer(r"\d{4}-\d{2}-\d{2}|20 October", text)]
    monkeypatch.setattr(engine, "model_findings", fake_model)
    monkeypatch.setattr(engine, "_model", lambda: None)
    assert not engine.mask("Remind me on 20 October to renew the domain", mode="full").masked
    assert not engine.mask("deploy window 2026-10-20 18:00", mode="full").masked
    assert engine.mask("Priya was born 1961-01-26 in Pune", mode="full").masked
    assert engine.mask("DOB: 1961-01-26", mode="full").masked
    assert engine.mask("geb. 1989-10-27", mode="full").masked and engine.mask("Né(e) le 1991-04-02", mode="full").masked


def test_mask_all_dates_setting(monkeypatch):
    import json
    from cloakroom import config, engine, rules
    monkeypatch.setattr(engine, "model_findings", lambda t: [rules.Finding(0, 10, "DATE", "model:private_date")])
    config.private_dir()
    (config.HOME / "config.json").write_text(json.dumps({"mask_all_dates": True}))
    assert engine.mask("2026-10-20 deploy", mode="full").masked


def test_date_next_to_a_name_is_personal():
    from cloakroom import engine
    t = "Patient: Vlad Popa (1998-01-19)"
    name = (t.index("Vlad"), t.index("Popa") + 4)
    d = t.index("1998")
    assert engine._personal_date(t, d, d + 10, [name])
    t = "Remind me to call Priya Raman on 2026-10-20"
    assert not engine._personal_date(t, t.index("2026"), len(t), [(t.index("Priya"), t.index("Raman") + 5)])
    t = "('MRN-1','Bram Peeters','2000-06-25','CLM-1')"
    assert engine._personal_date(t, t.index("2000"), t.index("2000") + 10, [(t.index("Bram"), t.index("Peeters") + 7)])


def test_role_words_are_not_names():
    from cloakroom import safe
    for v in ("Support Agent", "Support Conversational Log", "Service Team", "Phone Number", "Unknown"):
        assert safe.reason(v, "PERSON", False) == "job title or generic words, not a name", v
    for v in ("John Smart", "Mr. Green", "Sarah"):
        assert safe.reason(v, "PERSON", False) != "job title or generic words, not a name", v


def test_owner_column_of_a_file_listing_is_not_a_name(monkeypatch):
    import getpass
    from cloakroom import engine, rules
    monkeypatch.setattr(getpass, "getuser", lambda: "kai")
    text = ("-rw-r--r--@  1 kai  staff  1843 Oct 12 12:47 customers.md\n"
            "-rw-r--r--@  1 kai  staff  912 Oct 12 12:47 reply.md\n")

    def fake_model(t):
        out = []
        for m in re.finditer(r"kai  staff", t):
            out.append(rules.Finding(m.start(), m.end(), "PERSON", "model:private_person"))
        for m in re.finditer(r"Oct 12 12:47", t):
            out.append(rules.Finding(m.start(), m.end(), "DATE", "model:private_date"))
        return out
    monkeypatch.setattr(engine, "model_findings", fake_model)
    monkeypatch.setattr(engine, "_model", lambda: None)
    r = engine.mask(text)
    assert r.text == text and not r.masked
    # a real name that starts with the login is still a name
    monkeypatch.setattr(engine, "model_findings",
                        lambda t: [rules.Finding(0, 11, "PERSON", "model:private_person")])
    assert engine.mask("Kai Okafor wrote in").masked


def test_lower_case_claude_is_the_tool_not_a_person():
    from cloakroom import safe
    assert safe.reason("claude", "PERSON", False)
    assert safe.reason("Claude Moreau", "PERSON", False) is None


def test_digits_alone_are_never_a_name():
    from cloakroom import safe
    assert safe.reason("55", "PERSON", False) and safe.reason("1", "PERSON", False)
    assert safe.reason("Nadia", "PERSON", False) is None


def test_link_count_beside_own_login_is_still_the_owner_column(monkeypatch):
    import getpass
    from cloakroom import engine
    monkeypatch.setattr(getpass, "getuser", lambda: "kai")
    assert engine._own_account("1 kai  staff") and not engine._own_account("kai okafor")


def test_precision_round_scrubbed_templates_and_references():
    from cloakroom import safe
    # values that say they were scrubbed, and templates, are not secrets
    assert safe.reason("login_and_password_removed", "SECRET", True) == "already redacted"
    assert safe.reason("REDACTED_TOKEN", "SECRET", True) == "already redacted"
    assert safe.reason("$t(lockRoomPassword) annuleren", "SECRET", True)
    assert safe.reason("{{ vault_db_password }}", "SECRET", True)
    # a scrub word glued inside a longer token is not a scrub marker
    assert safe.reason("Zq8vT3mWremovedK2pL7n", "SECRET", True) is None
    # a variable named after the secret is a reference; a literal (even one holding the word) is still masked
    # (keyed-secret only: the keyed-password rule for password / pwd / pin keys is unchanged)
    for line in ("auth_token: session.authToken,", "token = opts.AccessToken;", 'secret="NATS_SECRET"',
                 "secret: dockerhub_password"):
        assert mask(line, Vault()).text == line, line
    for line in ('password: "Zq8vT3mW9xK2pL7n"', "token = Zq8vT3mW9xK2pL7n", 'secret: "mysecretvalue"',
                 "api_key=hunter2Password99"):
        assert "PII_SECRET" in mask(line, Vault()).text, line
