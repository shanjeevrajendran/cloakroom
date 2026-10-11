"""Generate a synthetic agent-traffic corpus: what a coding agent's tools return (files, logs, shell output).

Every value is fake and generated from a fixed seed. Each item lists the planted sensitive values; a tool
"leaks" when any planted value survives in its output. Negatives (no planted values) measure false alarms.

    python bench/gen_corpus.py > bench/corpus.jsonl
"""
import json
import random
import string

R = random.Random(20261009)

FIRST = ["Priya", "Marcus", "Elena", "Tomasz", "Aisha", "Kenji", "Lucia", "Oluwaseun", "Hannah", "Rafael",
         "Mei", "Dmitri", "Fatima", "Connor", "Ingrid", "Arjun", "Zanele", "Mateo", "Sofia", "Kwame"]
LAST = ["Raman", "Ellery", "Novak", "Kowalczyk", "Haddad", "Tanaka", "Ferreira", "Adeyemi", "Lindqvist",
        "Okafor", "Castellano", "Whitfield", "Brennan", "Sorensen", "Mbeki", "Duarte", "Iyer", "Vasquez"]
STREETS = ["Oak Street", "Maple Avenue", "Harbor Road", "Linden Lane", "Cedar Court", "Willow Way"]
CITIES = [("Portland", "OR", "97205"), ("Austin", "TX", "78701"), ("Raleigh", "NC", "27601"),
          ("Madison", "WI", "53703"), ("Boulder", "CO", "80302")]
DOMAINS = ["northwind-legal.com", "brightpath.io", "keystonemed.org", "fernhill.co", "alderbank.com"]


def rnd(chars, n):
    return "".join(R.choice(chars) for _ in range(n))


AZ09 = string.ascii_letters + string.digits
UP09 = string.ascii_uppercase + string.digits


def person():
    return f"{R.choice(FIRST)} {R.choice(LAST)}"


def email(name=None):
    name = name or person()
    f, l = name.lower().split()[0], name.lower().split()[-1]
    return f"{f}.{l}@{R.choice(DOMAINS)}"


def phone():
    return f"({R.randint(201, 989)}) 555-{R.randint(1000, 9999)}"


def address():
    c, s, z = R.choice(CITIES)
    return f"{R.randint(12, 9876)} {R.choice(STREETS)}, {c}, {s} {z}"


def ssn():
    return f"{R.randint(100, 665)}-{R.randint(10, 99)}-{R.randint(1000, 9999)}"


def card():
    digits = [4] + [R.randint(0, 9) for _ in range(14)]
    total = 0
    for i, d in enumerate(reversed(digits)):
        d = d * 2 if i % 2 == 0 else d
        total += d - 9 if d > 9 else d
    digits.append((10 - total % 10) % 10)
    s = "".join(map(str, digits))
    return f"{s[:4]} {s[4:8]} {s[8:12]} {s[12:]}"


def iban():
    bban = "NWBK" + "".join(str(R.randint(0, 9)) for _ in range(14))
    check = 98 - int("".join(str(int(c, 36)) for c in bban + "GB00")) % 97  # valid mod-97, like real IBANs
    return f"GB{check:02d}{bban}"


def dob():
    return f"{R.randint(1950, 2004)}-{R.randint(1, 12):02d}-{R.randint(1, 28):02d}"


def _b64(s):
    import base64
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


SECRETS = {
    "aws_key": lambda: "AKIA" + rnd(string.ascii_uppercase + "234567", 16),  # real key ids are base32
    "aws_secret": lambda: rnd(AZ09 + "/+", 40),
    "github_pat": lambda: "ghp_" + rnd(AZ09, 36),
    "anthropic_key": lambda: "sk-ant-api03-" + rnd(AZ09 + "-_", 93),
    "openai_key": lambda: "sk-proj-" + rnd(AZ09, 48),
    "stripe_key": lambda: "sk_live_" + rnd(AZ09, 24),
    "slack_token": lambda: "xoxb-" + "-".join(rnd(string.digits, 12) for _ in range(2)) + "-" + rnd(AZ09, 24),
    "hf_token": lambda: "hf_" + rnd(AZ09, 34),
    "db_password": lambda: rnd(AZ09 + "!#%^*", 18),
    "jwt": lambda: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + _b64(json.dumps({"sub": rnd(string.digits, 8), "name": person(), "iat": R.randint(1700000000, 1800000000)})) + "." + rnd(AZ09 + "_-", 43),
}


def secret(kind):
    return SECRETS[kind]()


ITEMS = []


def add(category, text, planted):
    ITEMS.append({"id": f"{category}-{sum(1 for i in ITEMS if i['category'] == category) + 1:03d}",
                  "category": category, "text": text, "planted": planted})


def gen_env():
    for _ in range(12):
        ks = R.sample(list(SECRETS), 3)
        vals = {k: secret(k) for k in ks}
        lines = ["# local dev settings", "NODE_ENV=development", "PORT=3000"]
        names = {"aws_key": "AWS_ACCESS_KEY_ID", "aws_secret": "AWS_SECRET_ACCESS_KEY", "github_pat": "GITHUB_TOKEN",
                 "anthropic_key": "ANTHROPIC_API_KEY", "openai_key": "OPENAI_API_KEY", "stripe_key": "STRIPE_SECRET_KEY",
                 "slack_token": "SLACK_BOT_TOKEN", "hf_token": "HF_TOKEN", "db_password": "DB_PASSWORD", "jwt": "SESSION_JWT"}
        lines += [f"{names[k]}={v}" for k, v in vals.items()]
        add("env_file", "\n".join(lines) + "\n", list(vals.values()))


def gen_config():
    for _ in range(10):
        pw, user, host = secret("db_password"), R.choice(["app", "admin", "svc_api"]), f"db{R.randint(1, 9)}.internal"
        k = secret(R.choice(["stripe_key", "openai_key", "slack_token"]))
        admin = person()
        mail = email(admin)
        cfg = {"database": {"url": f"postgres://{user}:{pw}@{host}:5432/prod"}, "payments": {"api_key": k},
               "owner": {"name": admin, "email": mail}, "features": {"beta": True}}
        add("json_config", json.dumps(cfg, indent=2), [pw, k, admin, mail])


def gen_logs():
    for _ in range(14):
        rows, planted = [], []
        for _ in range(R.randint(3, 6)):
            n = person()
            e, p = email(n), phone()
            ts = f"2026-10-0{R.randint(1, 8)}T{R.randint(10, 23)}:{R.randint(10, 59)}:{R.randint(10, 59)}Z"
            kind = R.choice(["signup", "payment_failed", "password_reset", "login"])
            if kind == "payment_failed":
                c = card()
                rows.append(f"{ts} WARN payment_failed user={e} card={c} reason=insufficient_funds")
                planted += [e, c]
            elif kind == "signup":
                rows.append(f"{ts} INFO signup name=\"{n}\" email={e} phone=\"{p}\"")
                planted += [n, e, p]
            else:
                rows.append(f"{ts} INFO {kind} user={e} ip=10.0.{R.randint(0, 9)}.{R.randint(2, 250)}")
                planted += [e]
        add("app_log", "\n".join(rows), planted)


def gen_csv():
    for _ in range(10):
        rows, planted = ["customer_id,name,email,phone,address,dob"], []
        for i in range(R.randint(3, 5)):
            n = person()
            e, p, a, d = email(n), phone(), address(), dob()
            rows.append(f"{1000 + i},{n},{e},{p},\"{a}\",{d}")
            planted += [n, e, p, a, d]
        add("csv_export", "\n".join(rows), planted)


def gen_code():
    for _ in range(12):
        k1, k2 = secret("openai_key"), secret("aws_key")
        n = person()
        e = email(n)
        src = (f"import boto3\nfrom openai import OpenAI\n\n# TODO(remove before commit) - {n} <{e}>\n"
               f"client = OpenAI(api_key=\"{k1}\")\ns3 = boto3.client(\"s3\", aws_access_key_id=\"{k2}\")\n\n"
               "def handler(event):\n    return s3.list_buckets()\n")
        add("source_code", src, [k1, k2, n, e])


def gen_shell():
    for _ in range(10):
        n1, n2 = person(), person()
        e1, e2 = email(n1), email(n2)
        out = (f"$ git log -2 --format='%h %an <%ae> %s'\n{rnd('0123456789abcdef', 7)} {n1} <{e1}> fix login redirect\n"
               f"{rnd('0123456789abcdef', 7)} {n2} <{e2}> bump deps\n")
        add("shell_git", out, [n1, e1, n2, e2])
    for _ in range(8):
        k, pw = secret("github_pat"), secret("db_password")
        out = f"$ env | grep -i -E 'token|pass'\nGITHUB_TOKEN={k}\nPGPASSWORD={pw}\n"
        add("shell_env", out, [k, pw])


def gen_notes():
    for _ in range(12):
        n = person()
        e, p, a, s = email(n), phone(), address(), ssn()
        kind = R.choice(["hr", "support", "contract"])
        if kind == "hr":
            t = f"# Onboarding\nNew hire {n} starts Monday. Home address {a}. SSN on the I-9: {s}. Personal cell {p}."
            planted = [n, a, s, p]
        elif kind == "support":
            t = f"Ticket #4821: {n} ({e}) says the export failed. Call back at {p}. Refund to IBAN {(ib := iban())}."
            planted = [n, e, p, ib]
        else:
            t = f"Contract signed by {n}, DOB {(d := dob())}, mailing address {a}, contact {e}."
            planted = [n, d, a, e]
        add("doc_note", t, planted)


def gen_key_block():
    for _ in range(4):
        body = "\n".join(rnd(AZ09 + "+/", 64) for _ in range(6))
        t = f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----\n"
        add("private_key", t, [body.splitlines()[0]])


def gen_negatives():
    """Clean traffic that looks risky: fixtures, docs, hashes, UUIDs, public addresses, placeholders."""
    negs = [
        "OPENAI_API_KEY=your-api-key-here\nAWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\nSTRIPE_SECRET_KEY=sk_test_xxxxxxxxxxxxxxxx\n",
        "def test_signup():\n    user = make_user(email=\"alice@example.com\", phone=\"555-0100\")\n    assert user.ok\n",
        "commit 3f9a2c1e8b7d6f5a4e3c2b1a0f9e8d7c6b5a4f3e\nAuthor: dependabot[bot] <support@github.com>\n    Bump lodash from 4.17.20 to 4.17.21\n",
        "Request id 7c9e6679-7425-40de-944b-e07fc1f90ae7 completed in 182ms (sha256: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08)\n",
        "## Contributing\nOpen an issue or email maintainers@opensource.org. See https://docs.python.org/3/library/re.html.\n",
        "Version 2.14.3 released 2026-09-30. Port 5432 open on 127.0.0.1. Build #48213 passed in 3m12s.\n",
        "SELECT id, created_at FROM orders WHERE status = 'shipped' AND total > 100 ORDER BY created_at DESC LIMIT 20;\n",
        "The Ada Lovelace and Alan Turing papers are classics of computing history; Grace Hopper wrote the first compiler.\n",
        "docker run -p 8080:80 -e LOG_LEVEL=debug nginx:1.27\n",
        "Error: ECONNREFUSED 127.0.0.1:6379 at TCPConnectWrapper.afterConnect [as oncomplete] (node:net:1555:16)\n",
        "password = os.environ[\"DB_PASSWORD\"]  # read from env, never hardcode\napi_key = settings.API_KEY\n",
        "Invoice template: {{customer_name}} <{{customer_email}}>, total {{amount}} due {{due_date}}.\n",
        "Our office: 1600 Amphitheatre Parkway, Mountain View, CA (public HQ address from the about page).\n",
        "tests/fixtures/cards.json: [\"4242 4242 4242 4242\", \"4000 0000 0000 0002\"]  # Stripe test cards\n",
        "Mean latency p50=41ms p95=118ms p99=240ms over 10,000 requests; error rate 0.02%.\n",
        "git remote -v\norigin  git@github.com:acme/widgets.git (fetch)\norigin  git@github.com:acme/widgets.git (push)\n",
        "Placeholder values: PII_EMAIL_1 and PII_PERSON_2 were restored locally.\n",
        "function slugify(s) { return s.toLowerCase().replace(/[^a-z0-9]+/g, '-'); }\n",
        "Readme: run `make test`. Requires Python 3.11+, Node 20, and Postgres 16.\n",
        "Weather service returned 72F, humidity 40%, wind 5 mph NW for zip 94043.\n",
    ]
    for t in negs:
        add("negative", t, [])


for g in (gen_env, gen_config, gen_logs, gen_csv, gen_code, gen_shell, gen_notes, gen_key_block, gen_negatives):
    g()

for item in ITEMS:
    print(json.dumps(item))
