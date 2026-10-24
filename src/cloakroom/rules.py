"""Pattern detectors: secrets with known shapes and PII with checksums. No model, no I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    start: int
    end: int
    kind: str       # placeholder type: SECRET, EMAIL, PHONE, PERSON, ADDRESS, CARD, SSN, IBAN, ACCOUNT, DATE, URL
    source: str     # rule id or "model:<label>"
    keyed: bool = False  # value came from "password=..."-style context: shape-based safe checks don't apply

    def value(self, text: str) -> str:
        return text[self.start:self.end]


def _luhn(digits: str) -> bool:
    total = 0
    for i, d in enumerate(int(c) for c in reversed(digits)):
        d = d * 2 if i % 2 else d
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def _iban_ok(raw: str) -> bool:
    s = raw.replace(" ", "")
    s = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in s)) % 97 == 1


# (id, kind, regex, group holding the value, validator)
SECRET_RULES = [
    ("private-key", "SECRET", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----", 0, None),
    ("aws-key-id", "SECRET", r"\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}\b", 0, None),
    ("github-token", "SECRET", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b", 0, None),
    ("anthropic-key", "SECRET", r"\bsk-ant-[A-Za-z0-9_-]{20,}", 0, None),
    ("openai-key", "SECRET", r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}", 0, None),
    ("stripe-key", "SECRET", r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b", 0, None),
    ("ssh-fingerprint", "SECRET", r"\bSHA256:[A-Za-z0-9+/]{40,44}={0,2}(?![A-Za-z0-9+/])|\bMD5:(?:[0-9a-f]{2}:){15}[0-9a-f]{2}\b", 0, None),
    ("twilio-sid", "SECRET", r"\b(?:AC|SK)[0-9a-f]{32}\b", 0, None),
    ("sendgrid-key", "SECRET", r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}", 0, None),
    ("slack-token", "SECRET", r"\bxox[abposr]-[A-Za-z0-9-]{10,}", 0, None),
    ("huggingface-token", "SECRET", r"\bhf_[A-Za-z0-9]{30,}\b", 0, None),
    ("google-api-key", "SECRET", r"\bAIza[0-9A-Za-z_-]{35}\b", 0, None),
    ("jwt", "SECRET", r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", 0, None),
    ("url-password", "SECRET", r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:([^\s@/]{3,})@", 1, None),
    ("url-username", "ACCOUNT", r"\b[a-z][a-z0-9+.-]*://([^\s:/@]+):[^\s@/]{3,}@", 1, lambda v: _personal_user(v)),
    # key = value / key: value / "key": "value" where the key names a secret
    # passwords and PINs are often short: from 4 characters, after markdown or quotes around the key
    ("keyed-password", "SECRET", r"(?i:\b(?:password|passwd|passcode|passphrase|pin(?:code)?|pwd))" + r"[\"'*_]*\s*[:=]\s*[*_]*\s*"
     r"(?:\"([^\"\n]{4,64})\"|'([^'\n]{4,64})'|([^\s\"]{4,64}))", 0, None),
    ("keyed-secret", "SECRET",
     r"(?i)(?:pass(?:word|wd|phrase)?|pwd|secret|token|api[_-]?key|access[_-]?key|auth[_-]?token|authorization|bearer|credentials?|private[_-]?key)"
     r"[\w.-]*[\"']?\s*[:=]\s*[\"']?(?:(?:Bearer|Basic|Token)\s+)?([^\s\"',;]{6,})", 1, None),
]

PII_RULES = [
    ("email", "EMAIL", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", 0, None),
    ("phone", "PHONE", r"(?<![\w.:-])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\w-])", 0, None),
    ("ssn", "SSN", r"(?<!\d)(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?!\d)", 0, None),
    ("card", "CARD", r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])", 0, lambda v: _luhn(re.sub(r"\D", "", v))),
    ("iban", "IBAN", r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b", 0, _iban_ok),
]

# Account names that identify no one: service users, CI runners, OS defaults, placeholders
_GENERIC_USERS = {"admin", "administrator", "root", "user", "username", "guest", "default", "public", "shared",
                  "all users", "default user", "runner", "runneradmin", "ubuntu", "ec2-user", "vagrant", "travis",
                  "circleci", "jenkins", "git", "oauth2", "x-access-token", "x-oauth-basic", "token", "api", "apikey",
                  "postgres", "mysql", "redis", "app", "deploy", "ci", "build", "docker", "me", "you", "test"}


def _personal_user(v: str) -> bool:
    return v.lower() not in _GENERIC_USERS and not re.match(r"^[<{$%]|^\.\.?$|^~", v)


PATH_RULES = [
    # sshd and similar auth logs: "Accepted publickey for <user> from", "Failed password for invalid user <user>"
    ("auth-log-user", "ACCOUNT", r"\b(?:Accepted|Failed) \w+ for (?:invalid user )?([\w.-]+) from\b", 1,
     lambda v: _personal_user(v)),
    # the user on a database/host line: "database: crm user jdoe", "host=db1 user=jdoe"
    ("db-line-user", "ACCOUNT", r"(?i)\b(?:database|db|host|schema|warehouse)\b[^\n]{0,60}?\buser(?:name)?\s*[=:]?\s*[\"']?"
     r"([A-Za-z][\w.-]{2,31})", 1, lambda v: _personal_user(v)),
    # the remote user in common/combined access logs: 203.0.113.9 - jdoe [10/Oct/2026:...]
    ("access-log-user", "ACCOUNT", r"(?m)^\S+ \S+ ([^\s\[\]\-][^\s\[\]]*) \[\d{1,2}/\w{3}/\d{4}", 1,
     lambda v: _personal_user(v)),
    # IAM users named in infrastructure code: aws_iam_access_key.<user>, aws_iam_user.<user>
    ("iam-user-resource", "ACCOUNT", r"\baws_iam_(?:user|access_key|user_policy|user_login_profile|user_ssh_key|"
     r"user_group_membership)\.([A-Za-z][\w-]+)", 1, lambda v: _personal_user(v)),
    # the account name in a home directory: /Users/<name>, /home/<name>, C:\Users\<name> (also JSON-escaped \\)
    ("home-dir-user", "USERNAME", r"(?:/Users/|/home/|\b[A-Za-z]:\\{1,2}(?:Users|Documents and Settings)\\{1,2})"
     r"([^\s/\\\"':<>|*?]{2,})", 1, lambda v: _personal_user(v)),
]

_PERSON = r"[A-Z][a-z'-]+(?: [A-Z][a-z'-]+){1,3}"
_STREET = (r"\b\d{1,6} [A-Z][a-z]+(?: [A-Z][a-z]+)* (?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Court|Ct|Way|"
           r"Boulevard|Blvd|Drive|Dr|Place|Pl|Terrace|Circle|Highway|Hwy)\b\.?"
           r"(?:,? (?:Apt|Suite|Unit|#) ?\w+)?(?:, [A-Z][a-z]+(?: [A-Z][a-z]+)*)?(?:,? [A-Z]{2})?(?: \d{5}(?:-\d{4})?)?")
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]{0,6}\.?"
_DATE = (r"(?:\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}\.\d{1,2}\.\d{4}|"
         r"\d{1,2}(?:st|nd|rd|th)? (?:of )?" + _MONTH + r",? \d{4}|" + _MONTH + r" \d{1,2}(?:st|nd|rd|th)?,? \d{4})")
# key, then quotes / markdown / an XML tag end, then : or = (optional after a tag or markdown)
_KEY_SEP = r"[\"'*_]*\s*(?:>|[:=])?\s*[\"'*_]*\s*"
_KEY_SEP_REQ = r"[\"'*_]*\s*(?:>|[:=])\s*[\"'*_]*\s*"  # the separator is required: "Main Street, Anytown" has no key

# PII that only a context gives away: a field name, a "Name <email>" pair, a street shape, a birth date label.
CONTEXT_RULES = [
    ("person-field", "PERSON",
     r"(?i:\b(?:full_?name|first_?name|last_?name|name|owner|author|customer|contact|employee|patient|signed by|"
     r"new hire|spoke with))[\"'*]*\s*[:=]?\s*[\"'*]*(" + _PERSON + r")", 1, None),
    ("person-email", "PERSON", r"(" + _PERSON + r")\s*[<(][\w.%+-]+@", 1, None),
    ("street-address", "ADDRESS", _STREET, 0, None),
    ("birth-date", "DATE", r"(?i:\b(?:dob|d\.o\.b\.?|date[ _]of[ _]birth|birth_?date|birthday|born(?: on)?))" + _KEY_SEP
     + "(" + _DATE + ")", 1, None),
    # the value under an address key in JSON / YAML / markdown / "Key: value": "street": "Branston Lane"
    ("address-field", "ADDRESS",
     r"(?i:(?<![\w.-])(?<!email )(?<!e-mail )(?<!email_)(?<!ip )(?<!ip_)(?<!mac )(?<!web )(?<!wallet )(?<!mailing list )"
     r"(?:street(?:[ _]?(?:address|name|line\d?))?|address(?:[ _]?line[ _]?\d)?|(?:home|mailing|postal|residential|"
     r"billing|shipping)[ _]address))" + _KEY_SEP_REQ + r"([^\n\"'<>|{}\[\]]{3,80}?)(?=\s*(?:[\"'<|\n]|,\s*\"|$))", 1,
     lambda v: any(c.isalpha() for c in v) and not any(c in v for c in "@&=") and "://" not in v),
]

# found under a key that says what the value is: shape-based "looks like a placeholder / a public place" checks don't apply
_KEYED = {"keyed-secret", "keyed-password", "url-password", "address-field"}  # (birth dates keep the "historical date" check)
_COMPILED = [(i, k, re.compile(rx), g, v) for i, k, rx, g, v in SECRET_RULES + PII_RULES + CONTEXT_RULES + PATH_RULES]
OWN_PLACEHOLDER = re.compile(r"\bPII_[A-Z]+_\d+(?:_[a-z2-7]{6})?\b")  # issued (signed) or from before 0.6.1

# CSV columns whose every cell is personal data
_CSV_COLUMNS = {"name": "PERSON", "full_name": "PERSON", "first_name": "PERSON", "last_name": "PERSON",
                "customer": "PERSON", "address": "ADDRESS", "street": "ADDRESS", "dob": "DATE",
                "date_of_birth": "DATE", "birth_date": "DATE", "ssn": "SSN", "phone": "PHONE", "email": "EMAIL"}


def _csv_cells(text: str) -> list[Finding]:
    lines = text.split("\n")
    if len(lines) < 2 or "," not in lines[0]:
        return []
    header = [h.strip().strip("\"'").lower() for h in lines[0].split(",")]
    cols = {i: _CSV_COLUMNS[h] for i, h in enumerate(header) if h in _CSV_COLUMNS}
    if not cols:
        return []
    out, pos = [], len(lines[0]) + 1
    cell = re.compile(r'"[^"]*"|[^,]*')
    for line in lines[1:]:
        col, i = 0, 0
        while i <= len(line):
            m = cell.match(line, i)
            start, end = m.span()
            if col in cols and end > start:
                s, e = (start + 1, end - 1) if line[start:end].startswith('"') else (start, end)
                if e > s:
                    out.append(Finding(pos + s, pos + e, cols[col], "csv-column"))
            col, i = col + 1, end + 1
        pos += len(line) + 1
    return out


# Segments of a variable name that say "this holds the secret": password = user.password, token: NATS_TOKEN
_SECRET_WORDS = {"password", "passwd", "passphrase", "pass", "pwd", "secret", "secrets", "token", "tokens", "key",
                 "keys", "apikey", "secretkey", "accesskey", "authtoken", "accesstoken", "auth", "credential",
                 "credentials", "cred", "creds"}


def _names_a_variable(text: str, start: int, end: int) -> bool:
    """The value under a secret key is itself a variable named after the secret: an unquoted identifier or dotted
    path that ends in the word (user.password, hashedPassword;, opts.AccessToken), or an UPPER_SNAKE constant
    (NATS_PASSWORD) even in quotes. A literal is still masked: quoted ("mysecret"), with digits (hunter2Password99),
    or not ending in the word."""
    v = text[start:end].rstrip(",;)}]")
    quoted = start > 0 and text[start - 1] in "\"'"
    if quoted and not re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+", v):
        return False
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_.]*", v):  # digits: could be a literal (hunter2Password99), keep masking
        return False
    segs = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+", v)
    return bool(segs) and segs[-1].lower() in _SECRET_WORDS  # ends in the word: user.password, opts.AccessToken


# a ChangeLog file entry: "* fast/forms/password-placeholder.png: Added." (WebKit and GNU style); the key is a file name
_CHANGELOG_ENTRY = re.compile(r"(?m)^[ \t]*\*[ \t]+[\w./+-]+:[ \t]+(?:Added|Removed|Modified|Copied|Renamed|Moved|Updated|"
                              r"Deleted|Replaced|Ditto)\b")


def _changelog_line(text: str, start: int) -> bool:
    m = _CHANGELOG_ENTRY.match(text, text.rfind("\n", 0, start) + 1)
    return m is not None and m.end() >= start


def scan(text: str) -> list[Finding]:
    out = []
    for rule_id, kind, rx, group, validate in _COMPILED:
        for m in rx.finditer(text):
            start, end = m.span(group)
            if start < 0 or (validate and not validate(m.group(group))):
                continue
            if rule_id == "keyed-secret" and _names_a_variable(text, start, end):
                continue
            if rule_id in ("keyed-secret", "keyed-password") and _changelog_line(text, start):
                continue
            if rule_id == "keyed-password":  # the value sits in whichever group matched (quoted or bare)
                g = next(i for i in (1, 2, 3) if m.group(i) is not None)
                start, end = m.span(g)
                if g == 3 and _names_a_variable(text, start, end):  # unquoted only: password: req.body.password
                    continue
            out.append(Finding(start, end, kind, rule_id,
                               keyed=rule_id in _KEYED))
    return out + _csv_cells(text) + _finance(text)


# US bank numbers (kind BANK). Digits alone say nothing, so a value counts only next to a word or field that names
# it: a routing number must also pass the ABA checksum; a plain "account" key counts only beside a routing number.
# A key glued to the digits by - or _ is an id prefix (ACCT-482913), not a label.
_GAP = r"[^\w\n$\u20ac\u00a3]{0,6}(?:(?i:number|num|no|is)\b[^\w\n$\u20ac\u00a3]{0,4})?"
_ROUTING = re.compile(r"(?<![A-Za-z])(?:[Rr]outing|ROUTING|ABA|[Aa]ba|RTN|[Rr]tn|[Tt]ransit|TRANSIT|[Bb]ank|BANK)"
                      r"(?:[ _-]?(?:[Nn]umber|NUMBER|[Nn]um|NUM|[Nn]o|NO|[Cc]ode|CODE|#))?(?![a-z])(?![-_]\d)" + _GAP
                      + r"(\d{9})(?![\d-]|[.,]\d)")
_ACCOUNT = re.compile(r"(?i:(?<![a-z])(?:account[ _-]?(?:number|num|no|nbr|#)|acct(?:[ _-]?(?:number|num|no|nbr|#))?|"
                      r"(?:checking|savings|bank|beneficiary|deposit)[ _-]?(?:account|acct)(?:[ _-]?(?:number|num|no|#))?|"
                      r"dda(?:[ _-]?(?:number|num|no|#))?))(?![a-z])(?![-_]\d)" + _GAP
                      + r"(\d{2,}(?:[ -]\d{2,})*)(?![\d-]|[.,]\d)")
# a plain account key in the same record as a routing number: {"routing": ..., "account": "..."}
_PAIRED = re.compile(r"(?i:(?<![a-z])(?:account|acct)(?:[ _-]?(?:id|num|number|no))?)(?![a-z])" + _KEY_SEP_REQ
                     + r"(\d(?:-?\d){3,16})(?![\d-]|[.,]\d)")
# NACHA (ACH file) entry detail record: 94 characters, "6", a transaction code, the RDFI routing number, the account
_NACHA = re.compile(r"(?m)^6(?:2[2-9]|3[2-9]|4[1-9]|5[1-5])(\d{9})([0-9A-Za-z -]{17})\d{10}.{55}$")
_CSV_COLUMNS.update({c: "BANK" for c in (
    "routing_number", "routing_no", "aba", "aba_number", "aba_routing", "rtn", "account_number", "account_no",
    "acct_number", "acct_no", "bank_account", "bank_account_number", "checking_account", "savings_account", "dda")})


def _aba_ok(d: str) -> bool:
    """ABA checksum (weights 3-7-1) and a Federal Reserve prefix: 00-12, 21-32, 61-72, 80."""
    p = int(d[:2])
    return (p <= 12 or 21 <= p <= 32 or 61 <= p <= 72 or p == 80) and len(set(d)) > 1 \
        and sum(int(c) * w for c, w in zip(d, (3, 7, 1) * 3)) % 10 == 0


def _finance(text: str) -> list[Finding]:
    routing = [Finding(*m.span(1), "BANK", "routing-number") for m in _ROUTING.finditer(text) if _aba_ok(m.group(1))]
    out = list(routing)
    for m in _NACHA.finditer(text):
        if _aba_ok(m.group(1)) and len(m.group()) == 94:
            acct = m.group(2).strip()
            a = m.start(2) + m.group(2).index(acct) if acct else 0
            out += [Finding(*m.span(1), "BANK", "nacha-entry")] + ([Finding(a, a + len(acct), "BANK", "nacha-entry")]
                                                                   if acct else [])
    for rx, rule_id in ((_ACCOUNT, "bank-account"), (_PAIRED, "bank-account-paired")):
        for m in rx.finditer(text):
            a, b = m.span(1)
            digits = re.sub(r"\D", "", m.group(1))
            if not 4 <= len(digits) <= 17 or len(set(digits)) == 1 or any(r.start == a for r in routing):
                continue
            if rule_id == "bank-account-paired" and not any(  # same record: within 300 chars, no blank line between
                    abs(r.start - a) <= 300 and "\n\n" not in text[min(r.start, a):max(r.end, b)] for r in routing):
                continue
            out.append(Finding(a, b, "BANK", rule_id))
    return out
