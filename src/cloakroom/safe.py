"""Known-safe filter: drop findings that are documented examples, test fixtures, public or technical values.

Applied after detection, to rule and model findings alike. Every check is listed here and covered by the
benchmark: a check that lets a planted value through is a leak, so changes must keep leaks at 0.
"""
from __future__ import annotations

import ipaddress
import re

EXAMPLE_MARKERS = re.compile(r"(?i)example|dummy|placeholder|changeme|your[-_ ]|xxxx|<[^>]+>|\{\{|\$\{|\*\*\*\*|^test[-_]|^fake")
# AWS's documentation account ids and sample key ids (KMS, ACM, IAM examples), and a UUID that is one hex digit
# repeated per group (11111111-1111-1111-1111-111111111111). Whole values only: a real key id inside an ARN with the
# documentation account (arn:aws:kms:...:111122223333:key/4e32...) stays masked.
AWS_DOC_ID = re.compile(r"111122223333|444455556666|123456789012|1234abcd-12ab-34cd-56ef-1234567890ab|"
                        r"0987dcba-09fe-87dc-65ba-ab0987654321|12345678-1234-1234-1234-123456789012|"
                        r"(?i:([0-9a-f])\1{7}-\1{4}-\1{4}-\1{4}-\1{12})")
AWS_ARN = re.compile(r"arn:aws[\w-]*:[\w-]*:[\w-]*:(\d{12})?:(?:[\w-]+[/:])*([^/:\s]+)")


def _aws_doc_example(v: str) -> bool:
    m = AWS_ARN.fullmatch(v)
    if m:  # the resource id decides: a documented sample, or a plain name under the documentation account
        return AWS_DOC_ID.fullmatch(m.group(2)) is not None or (
            m.group(1) is not None and AWS_DOC_ID.fullmatch(m.group(1)) is not None and not re.search(r"\d", m.group(2)))
    return AWS_DOC_ID.fullmatch(v) is not None


ENV_REFERENCE = re.compile(r"(?:os\.environ|os\.getenv|process\.env|ENV\[|getenv\(|settings\.|config\.)"
                           r"|^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$|^%[A-Za-z_][A-Za-z0-9_]*%$")
# a template, not a literal: $t(key) ..., ${var}..., {{var}}, %(name)s, {0}
TEMPLATE_START = re.compile(r"^(?:\$[A-Za-z_]\w*\(|\$\{|\{\{|%\(|\{\d*\})")
TOLL_FREE = re.compile(r"^(?:\+?1[\s.-]?)?\(?8(?:00|33|44|55|66|77|88)\)?[\s.-]")  # business lines, not people
TWILIO_TEST_PHONE = re.compile(r"^1?500555\d{4}$")  # Twilio's documented magic test numbers
TEST_KEY = re.compile(r"^(?:sk|pk|rk)[_-]test[_-]")
REDACTED = re.compile(r"(?i)^[\[<*]*(?:redacted|masked|hidden|removed|filtered|omitted)[\]>*]*$|^\*{3,}$|^x{4,}$")
# a scrubbed value that says so in one of its words: login_and_password_removed, REDACTED_TOKEN, secret-masked
SCRUBBED_WORD = re.compile(r"(?i)(?:^|[_\-.\s])(?:redacted|removed|masked|hidden|omitted|filtered|scrubbed|stripped|"
                           r"saniti[sz]ed|censored|obfuscated|elided)(?:$|[_\-.\s])")
PLACEHOLDER_NAME = re.compile(r"(?i)^(?:john|jane|richard|baby) doe$|^(?:john|jane) (?:smith|q\.? public)$")
UK_NINO_TEST = re.compile(r"^QQ ?\d{2} ?\d{2} ?\d{2} ?[A-D]$")  # HMRC never issues the QQ prefix
TIME_OR_EXPIRY = re.compile(r"(?i)^\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?$|^(?:0?[1-9]|1[0-2])/\d{2}$"
                            r"|^\d{1,2}:\d{2}(?::\d{2})?(?:\s*(?:-|–|to)\s*\d{1,2}:\d{2}(?::\d{2})?)?(?:\s*[A-Z]{2,4})?$")
DATE_NO_YEAR = re.compile(r"(?i)^\d{1,2}(?:st|nd|rd|th)? [a-z]{3,9}$|^[a-z]{3,9} \d{1,2}(?:st|nd|rd|th)?$")
# documentation-only IP ranges (RFC 5737, RFC 3849): never a real host
DOC_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")]
KEY_PREFIX_ONLY = re.compile(r"^(?:AIza[\w-]{0,8}|AKIA[A-Z2-7]{0,6}|gh[pousr]_\w{0,6}|sk[_-](?:live|test|proj)[_-]?\w{0,4}|"
                             r"xox[abposr]-?[\w-]{0,4}|eyJ[\w-]{0,8})(?:\.{3}|…|\*+)?$")
# Historical and public figures who show up in docs, examples and prose; matched on the whole name only
FAMOUS_PEOPLE = {n.lower() for n in (
    "Isaac Newton", "Albert Einstein", "Nikola Tesla", "Thomas Edison", "Marie Curie", "Charles Darwin",
    "Galileo Galilei", "Ada Lovelace", "Alan Turing", "Grace Hopper", "Charles Babbage", "Leonardo da Vinci",
    "William Shakespeare", "Jane Austen", "Mark Twain", "Charles Dickens", "Leo Tolstoy", "Wolfgang Amadeus Mozart",
    "Ludwig van Beethoven", "Johann Sebastian Bach", "Vincent van Gogh", "Pablo Picasso", "Abraham Lincoln",
    "George Washington", "Thomas Jefferson", "Benjamin Franklin", "Winston Churchill", "Napoleon Bonaparte",
    "Mahatma Gandhi", "Nelson Mandela", "Martin Luther King", "Queen Victoria", "Julius Caesar", "Cleopatra",
    "Aristotle", "Plato", "Socrates", "Confucius", "Marco Polo", "Christopher Columbus", "Amelia Earhart",
    "Florence Nightingale", "Louis Pasteur", "Gregor Mendel", "Dmitri Mendeleev", "Max Planck", "Niels Bohr",
    "Richard Feynman", "Stephen Hawking", "Carl Sagan", "Johannes Kepler", "Nicolaus Copernicus",
    "Rosalind Franklin", "Katherine Johnson", "John von Neumann", "Claude Shannon", "Dennis Ritchie",
    "Linus Torvalds", "Tim Berners-Lee", "Alexander Graham Bell", "Wright Brothers", "Henry Ford",
    "Frida Kahlo", "Sigmund Freud", "Karl Marx", "Rabindranath Tagore", "Srinivasa Ramanujan", "C. V. Raman")}
# Documented public addresses (company headquarters, landmarks): visitor info, not anyone's home
PUBLIC_ADDRESSES = re.compile(r"(?i)1600 Amphitheatre Parkway|One Apple Park Way|1 Apple Park Way|One Microsoft Way|"
                              r"1 Hacker Way|410 Terry Ave(?:nue)? N|1600 Pennsylvania Ave(?:nue)?|350 Fifth Avenue|"
                              r"1 Infinite Loop|500 Oracle Parkway|1 Market St(?:reet)?,? San Francisco")
JWT_IO_EXAMPLE = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
RESERVED_DOMAINS = re.compile(r"(?i)@(?:[\w-]+\.)*(?:example\.(?:com|org|net)|test|invalid|localhost|example)$")
ROLE_MAILBOX = re.compile(r"(?i)^(?:no-?reply|do-?not-?reply|support|info|help|hello|contact|maintainers?|security|"
                          r"admin|abuse|postmaster|webmaster|notifications?|team|press|sales|billing|privacy|git|"
                          r"[\w.-]*bot)(?:\+[\w.-]*)?@")
TEST_CARDS = {"4242424242424242", "4000000000000002", "4111111111111111", "5555555555554444", "5105105105105100",
              "378282246310005", "371449635398431", "6011111111111117", "3056930009020004", "4000056655665556"}
# NANP: 555-01xx is reserved for fiction, 555-1212 is directory assistance. Other 555 numbers stay masked:
# synthetic data (and people) use them as stand-ins for real numbers.
FICTIONAL_PHONE = re.compile(r"555[\s.-]?01\d\d$|555[\s.-]?1212$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
HEX_DIGEST = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})$", re.I)
PUBLIC_GIT = re.compile(r"(?i)^(?:git@|https://)(?:github\.com|gitlab\.com|bitbucket\.org)[:/][\w.-]+/[\w.-]+?(?:\.git)?$")
BOT_NAME = re.compile(r"(?i)\[bot\]|bot$")
# epoch and "unknown" defaults that forms and databases fill in: 1970-01-01, 01/01/1970, 1900-01-01 (any order)
DEFAULT_DATE = re.compile(r"^(?:(?:19(?:70|00))[-/.]0?1[-/.]0?1|0?1[-/.]0?1[-/.](?:19(?:70|00))|0?1 jan(?:uary)? (?:1970|1900)|"
                          r"jan(?:uary)? 0?1,? (?:1970|1900))$", re.I)
ELLIPSIS = re.compile(r"\.{3}|…")
# a package version or range (^1.0.5, ~2.3.0, 0.76.8): parts of 1-3 digits, so a date (12.05.1984) never matches
VERSION_RANGE = re.compile(r"^[\^~]\d{1,3}(?:\.\d{1,3}){1,2}(?:-[\w.]+)?$")
VERSION = re.compile(r"^v?\d{1,3}\.\d{1,3}\.\d{1,3}(?:-[\w.]+)?$")
CODE_LOCATION = re.compile(r"^(?=[\w.-]*[A-Za-z])[\w.-]+:\d+(?::\d+)?$")  # node:net:1555:16, file.py:42 (not IP:port)


def _this_year() -> int:
    import datetime
    return datetime.date.today().year


def _doc_ips_only(v: str) -> bool:
    found = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", v)
    if not found or re.sub(r"[\d.,;\s]", "", v):
        return False
    try:
        return all(any(ipaddress.ip_address(ip) in n for n in DOC_NETS) for ip in found)
    except ValueError:
        return False


def _alphabet_run(v: str) -> bool:
    letters = re.sub(r"[^a-z]", "", v.lower())
    return len(letters) >= 8 and letters[:8] in "abcdefghijklmnopqrstuvwxyz"


def _sequential(digits: str) -> bool:
    return len(digits) >= 8 and (digits in "01234567890123456789" or digits in "98765432109876543210")


def _year(v: str) -> int | None:
    m = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", v)
    return int(m.group(1)) if m else None


def _filler(v: str) -> bool:
    """AC00000000000000000000000000000000, sk-...your-key-here..., ghp_xxxx...: a value whose varying part (after a
    short letter prefix such as AC, sk-, ghp_) is one character repeated 8+ times, or that is cut short with an
    ellipsis, with no run of more than 8 letters and digits left in it (a real key is longer)."""
    if ELLIPSIS.search(v):
        return max((len(r) for r in re.findall(r"[A-Za-z0-9]+", v)), default=0) <= 8
    m = re.fullmatch(r"([A-Za-z]{0,4}[_-]?)([A-Za-z0-9])\2{7,}", v)
    return m is not None


def _is_internal_ip(v: str) -> bool:
    v = v.split(":")[0].strip("[]")
    if v.lower() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(v)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_unspecified or ip.is_link_local


# "Support Agent", "Service Team", "Phone Number": the model sometimes reads these as names. Only phrases made
# entirely of these words are skipped, so a real surname that is also a word (Smart, Green) is still masked beside
# a first name.
ROLE_WORDS = set("""
support agent agents team teams log logs conversational conversation chat transcript customer customers client clients
service services representative rep manager managers officer advisor adviser analyst assistant associate department
desk staff unknown phone number account user users admin administrator system operator bot helpdesk help center centre
director head lead specialist consultant clerk teller underwriter broker trader compliance claims adjuster relationship
branch loan financial sales operations ops hr it legal finance billing technical tech care success due diligence
the of and for on call caller recipient sender owner member members applicant borrower lender patient guest visitor
""".split())


def reason(value: str, kind: str, keyed: bool) -> str | None:
    """Why this finding is safe to leave as is, or None if it must be masked."""
    v = value.strip().strip("\"'.,;()[]")
    if EXAMPLE_MARKERS.search(v):
        return "example value"
    if _aws_doc_example(v):
        return "AWS documentation example id"
    if ENV_REFERENCE.search(v) or TEMPLATE_START.match(v):
        return "reference to a variable, not a value"
    if TEST_KEY.match(v):
        return "test-mode key"
    if REDACTED.match(v) or SCRUBBED_WORD.search(v):
        return "already redacted"
    if v.startswith(JWT_IO_EXAMPLE):
        return "jwt.io documentation example"
    if kind == "DATE" and DEFAULT_DATE.match(v):
        return "default placeholder date"
    if kind in ("SECRET", "ACCOUNT") and VERSION_RANGE.match(v):
        return "package version range"  # (minipass-flush: ^1.0.5 is a lockfile line, not a password)
    if keyed:  # a value named by a secret label: only explicit example markers above can clear it
        return None
    if kind in ("SECRET", "ACCOUNT") and VERSION.match(v):
        return "version number"
    if _doc_ips_only(v):
        return "documentation IP range"
    if kind == "SECRET" and KEY_PREFIX_ONLY.match(v.lstrip("=:\"' ")):
        return "truncated or prefix-only key"
    if kind == "SECRET" and (_alphabet_run(v.split()[-1]) or re.fullmatch(r"\d{6,}", v) and v in "01234567890123456789"):
        return "alphabet or counting placeholder"
    if kind in ("SECRET", "ACCOUNT") and _filler(value.strip()):
        return "filler placeholder (one repeated character, or cut short with ...)"
    if kind == "PERSON" and re.fullmatch(r"[a-z]{1,3}", v):
        return "too short to be a name"
    if kind == "PERSON" and not re.search(r"[^\W\d_]", v):
        return "no letters, not a name"
    if kind == "PERSON" and v == "claude":  # lower case: the CLI, the .claude folder; a person is "Claude"
        return "the claude command or folder"
    if kind == "PERSON" and v.lower() in FAMOUS_PEOPLE:
        return "historical or public figure"
    if kind == "PERSON" and all(w in ROLE_WORDS for w in re.findall(r"[a-z]+", v.lower())) and re.search(r"[a-z]", v.lower()):
        return "job title or generic words, not a name"
    digits = re.sub(r"\D", "", v)
    if kind in ("CARD", "ACCOUNT", "BANK", "PHONE", "SSN") and len(digits) >= 6 and len(set(digits)) == 1:
        return "repeated-digit placeholder"
    if kind in ("CARD", "ACCOUNT", "BANK", "PHONE", "SSN") and _sequential(digits):
        return "sequential-digit placeholder"
    if kind in ("CARD", "ACCOUNT", "BANK", "PHONE", "SSN") and re.fullmatch(r"(?:(\d)\1{2,}){3,}", digits):
        return "repeated-group placeholder"
    if kind == "ACCOUNT" and re.fullmatch(r"(?i)[0-9a-f]{4,}(?:-[0-9a-f]{2,})+-?", v) and re.search(r"(?i)[a-f]", v):
        return "UUID or hash fragment"
    if kind == "PHONE" and (TOLL_FREE.match(v) or TWILIO_TEST_PHONE.match(digits)):
        return "toll-free or test number"
    if kind == "DATE" and re.fullmatch(r"\d{1,2}\s+\d{1,2}", v):
        return "not a full date"
    if kind in ("CARD", "ACCOUNT") and digits in TEST_CARDS:
        return "published test card"
    if UK_NINO_TEST.match(v):
        return "HMRC test NI number"
    if kind == "PERSON" and PLACEHOLDER_NAME.match(v):
        return "placeholder name"
    if kind == "DATE" and not re.search(r"\d", v):
        return "not a date"
    if kind == "DATE" and DATE_NO_YEAR.match(v):
        return "date without a year"
    if kind == "DATE" and (TIME_OR_EXPIRY.match(v) or _year(v) and _year(v) < _this_year() - 110):
        return "time, expiry or historical date"
    if kind == "ADDRESS" and (PUBLIC_ADDRESSES.search(v) or not re.search(r"\d", v)):
        return "public address or place name without a street"
    if kind == "URL" and _is_internal_ip(re.sub(r"^[a-z][a-z0-9+.-]*://", "", v).split("/")[0]):
        return "loopback or private URL"
    if kind == "EMAIL" and (RESERVED_DOMAINS.search(v) or ROLE_MAILBOX.match(v)):
        return "reserved domain or role mailbox"
    if kind == "PHONE" and FICTIONAL_PHONE.search(v):
        return "fictional 555-01xx number"
    if _is_internal_ip(v):
        return "loopback or private IP"
    if UUID.match(v) or HEX_DIGEST.match(v):
        return "UUID or hash"
    if PUBLIC_GIT.match(v):
        return "public git remote"
    if kind == "PERSON" and BOT_NAME.search(v):
        return "bot account"
    if kind == "URL" and not re.match(r"^[a-z][a-z0-9+.-]*://", v) and re.match(r"^(?:/|~/|\./|\.\./)", v):
        return "local file path"
    if CODE_LOCATION.match(v) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?", v):
        return "code location"  # (an ISO date-time like 1994-06-18T00:00:00 is not file:line:col)
    if kind == "ADDRESS" and re.fullmatch(r"\d{5}(?:-\d{4})?", v):
        return "bare ZIP code"
    return None
