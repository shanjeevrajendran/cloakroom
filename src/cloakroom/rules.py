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
    ("home-dir-user", "ACCOUNT", r"(?:/Users/|/home/|\b[A-Za-z]:\\{1,2}(?:Users|Documents and Settings)\\{1,2})"
     r"([^\s/\\\"':<>|*?]{2,})", 1, lambda v: _personal_user(v)),
]

_PERSON = r"[A-Z][a-z'-]+(?: [A-Z][a-z'-]+){1,3}"
_STREET = (r"\b\d{1,6} [A-Z][a-z]+(?: [A-Z][a-z]+)* (?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Court|Ct|Way|"
           r"Boulevard|Blvd|Drive|Dr|Place|Pl|Terrace|Circle|Highway|Hwy)\b\.?"
           r"(?:,? (?:Apt|Suite|Unit|#) ?\w+)?(?:, [A-Z][a-z]+(?: [A-Z][a-z]+)*)?(?:,? [A-Z]{2})?(?: \d{5}(?:-\d{4})?)?")
_DATE = r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2} [A-Z][a-z]{2,8} \d{4})"

# PII that only a context gives away: a field name, a "Name <email>" pair, a street shape, a birth date label.
CONTEXT_RULES = [
    ("person-field", "PERSON",
     r"(?i:\b(?:full_?name|first_?name|last_?name|name|owner|author|customer|contact|employee|patient|signed by|"
     r"new hire|spoke with))[\"'*]*\s*[:=]?\s*[\"'*]*(" + _PERSON + r")", 1, None),
    ("person-email", "PERSON", r"(" + _PERSON + r")\s*[<(][\w.%+-]+@", 1, None),
    ("street-address", "ADDRESS", _STREET, 0, None),
    ("birth-date", "DATE", r"(?i:\b(?:dob|d\.o\.b\.?|date of birth|birth_?date|born(?: on)?))[\"']?\s*[:=]?\s*[\"']?(" + _DATE + ")", 1, None),
]

_COMPILED = [(i, k, re.compile(rx), g, v) for i, k, rx, g, v in SECRET_RULES + PII_RULES + CONTEXT_RULES + PATH_RULES]
OWN_PLACEHOLDER = re.compile(r"\bPII_[A-Z]+_\d+\b")

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


def scan(text: str) -> list[Finding]:
    out = []
    for rule_id, kind, rx, group, validate in _COMPILED:
        for m in rx.finditer(text):
            start, end = m.span(group)
            if start < 0 or (validate and not validate(m.group(group))):
                continue
            out.append(Finding(start, end, kind, rule_id, keyed=rule_id in ("keyed-secret", "url-password")))
    return out + _csv_cells(text)
