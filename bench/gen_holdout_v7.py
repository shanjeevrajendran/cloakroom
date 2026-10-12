"""Holdout v7: credentials in the formats vendors really issue, plus the shapeless passwords and encoded emails that
maisecrets' maintainers listed as open gaps (Mcpgate-de/maisecrets#17). Written after Cloakroom 0.2.0, run once,
never used for tuning.

    python bench/gen_holdout_v7.py > bench/holdout_v7.jsonl

Every value is random (fixed seed); no literal key appears in this file, so secret scanners have nothing to flag.
Formats (public vendor docs and the gitleaks / betterleaks rules):
- OpenAI project key: sk-proj- + 74 + T3BlbkFJ + 74 (base64url); legacy sk- + 20 + T3BlbkFJ + 20
- Anthropic: sk-ant-api03- + 93 base64url + AA
- Slack: xoxb-<10-13 digits>-<10-13 digits>-<24 alnum>; xoxp-<digits>-<digits>-<digits>-<32 hex>
- AWS key id AKIA/ASIA + 16 base32 ([A-Z2-7]); secret 40 base64
- GitHub ghp_ + 36; github_pat_ + 22 + _ + 59; GitLab glpat- + 20; npm_ + 36; Hugging Face hf_ + 34 letters
- Stripe sk_live_ / rk_live_ + 24 or 99; Google AIza + 35; SendGrid SG. + 22 + . + 43; Twilio API key SK + 32 hex
- Twilio Account SID AC + 32 hex is reported apart (category twilio_sid): it names the account, not a secret
"""
import base64
import json
import random
import string

R = random.Random(20261012)
ALNUM = string.ascii_letters + string.digits
B64URL = ALNUM + "-_"
B32 = string.ascii_uppercase + "234567"
HEX = "0123456789abcdef"
items = []


def rnd(alphabet, n):
    return "".join(R.choice(alphabet) for _ in range(n))


def digits(n):
    return str(R.randint(1, 9)) + rnd(string.digits, n - 1)


def add(cat, text, planted):
    items.append({"id": f"h7-{len(items) + 1:03d}", "category": cat, "text": text, "planted": planted})


KEYS = {
    "openai_project": lambda: "sk-proj-" + rnd(B64URL, 74) + "T3BlbkFJ" + rnd(B64URL, 74),
    "openai_legacy": lambda: "sk-" + rnd(ALNUM, 20) + "T3BlbkFJ" + rnd(ALNUM, 20),
    "anthropic": lambda: "sk-ant-api03-" + rnd(B64URL, 93) + "AA",
    "slack_bot": lambda: f"xoxb-{digits(12)}-{digits(13)}-{rnd(ALNUM, 24)}",
    "slack_user": lambda: f"xoxp-{digits(12)}-{digits(12)}-{digits(13)}-{rnd(HEX, 32)}",
    "aws_key_id": lambda: R.choice(["AKIA", "ASIA"]) + rnd(B32, 16),
    "github_classic": lambda: "ghp_" + rnd(ALNUM, 36),
    "github_fine": lambda: "github_pat_" + rnd(ALNUM, 22) + "_" + rnd(ALNUM, 59),
    "gitlab": lambda: "glpat-" + rnd(B64URL, 20),
    "npm": lambda: "npm_" + rnd(ALNUM, 36),
    "huggingface": lambda: "hf_" + rnd(string.ascii_letters, 34),
    "stripe_secret": lambda: R.choice(["sk", "rk"]) + "_live_" + rnd(ALNUM, R.choice([24, 99])),
    "google_api": lambda: "AIza" + rnd(B64URL, 35),
    "sendgrid": lambda: "SG." + rnd(B64URL, 22) + "." + rnd(B64URL, 43),
    "twilio_api_key": lambda: "SK" + rnd(HEX, 32),
}
VARS = {"openai_project": "OPENAI_API_KEY", "openai_legacy": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
        "slack_bot": "SLACK_BOT_TOKEN", "slack_user": "SLACK_USER_TOKEN", "aws_key_id": "AWS_ACCESS_KEY_ID",
        "github_classic": "GITHUB_TOKEN", "github_fine": "GH_TOKEN", "gitlab": "GITLAB_TOKEN", "npm": "NPM_TOKEN",
        "huggingface": "HF_TOKEN", "stripe_secret": "STRIPE_SECRET_KEY", "google_api": "GOOGLE_API_KEY",
        "sendgrid": "SENDGRID_API_KEY", "twilio_api_key": "TWILIO_API_KEY"}


def contexts(kind, key):
    var = VARS[kind]
    host = R.choice(["api.internal", "deploy-01", "ci-runner-7", "build.local"])
    return [
        (f"# .env\n{var}={key}\nDEBUG=false\n", "env"),
        (f"$ export {var}='{key}'\n$ ./run.sh --region eu-west-1\n", "shell"),
        (f'client = Client(api_key="{key}", timeout=30)\n', "code"),
        (f"jobs:\n  deploy:\n    env:\n      {var}: {key}\n    runs-on: ubuntu-latest\n", "ci_yaml"),
        (f"2026-10-12T09:14:02Z {host} WARN auth failed for token {key} (401)\n", "log"),
        (f"hey, can you check why this one stopped working? {key} it was fine yesterday", "chat"),
        (json.dumps({"service": kind, "credentials": {"token": key}, "enabled": True}, indent=2) + "\n", "json"),
    ]


# keys: every kind in three different contexts
for kind, make in KEYS.items():
    for text, ctx in R.sample(contexts(kind, "{KEY}"), 3):
        key = make()
        add(f"key_{kind}", text.replace("{KEY}", key), [key])

# AWS pair: id + secret together
for _ in range(4):
    kid, sec = "AKIA" + rnd(B32, 16), rnd(ALNUM + "+/", 40)
    add("key_aws_pair", f"[default]\naws_access_key_id = {kid}\naws_secret_access_key = {sec}\nregion = us-east-1\n",
        [kid, sec])

# Twilio Account SID with its auth token (reported apart: the SID names the account)
for _ in range(3):
    sid, tok = "AC" + rnd(HEX, 32), rnd(HEX, 32)
    add("twilio_sid", f"TWILIO_ACCOUNT_SID={sid}\nTWILIO_AUTH_TOKEN={tok}\n", [sid, tok])

# passwords without a known shape, in the forms the maintainers listed
PW = lambda: rnd(ALNUM, R.randint(3, 6)) + R.choice("!#%&*-_") + rnd(ALNUM, R.randint(4, 8))
for _ in range(2):
    pw = PW()
    add("pw_netrc", f"machine git.example-corp.net\n  login deploy\n  password {pw}\n", [pw])
    pw = PW()
    add("pw_cli_flag", f"$ pg_dump --host db1 --username app --password '{pw}' prod > dump.sql\n", [pw])
    pw = PW()
    add("pw_mysql", f"$ mysql -h 10.2.0.14 -u report -p'{pw}' analytics\n", [pw])
    pw = PW()
    add("pw_redis", f"127.0.0.1:6379> AUTH {pw}\nOK\n127.0.0.1:6379> KEYS session:*\n", [pw])
    pw = PW()
    add("pw_prose", f"the staging db is down again, my password is {pw} if you want to try logging in yourself", [pw])
    pw = PW()
    add("pw_url", f"DATABASE_URL=postgres://app:{pw}@db.prod.internal:5432/orders\n", [pw])

# emails with the @ percent-encoded
for first, last, dom in [("lena", "okafor", "brightfield.co"), ("tomas", "reyes", "quillfield.com"),
                         ("ines", "carvalho", "marrowgate.io"), ("dev", "patel", "tessellate.org")]:
    enc = f"{first}.{last}%40{dom}"
    add("email_encoded", f"GET /api/users?email={enc}&expand=orders HTTP/1.1\nHost: shop.internal\n", [enc])

# a private key and a JWT
pem = "-----BEGIN OPENSSH PRIVATE KEY-----\n" + "\n".join(rnd(ALNUM + "+/", 70) for _ in range(6)) + \
      "\n-----END OPENSSH PRIVATE KEY-----"
add("key_pem", f"cat ~/.ssh/deploy_ed25519\n{pem}\n", [pem.splitlines()[1]])
b64 = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
jwt = b64({"alg": "HS256", "typ": "JWT"}) + "." + b64({"sub": digits(8), "role": "admin", "exp": 1792000000}) + \
      "." + rnd(B64URL, 43)
add("key_jwt", f"Authorization: Bearer {jwt}\n", [jwt])

# negatives: documented examples, placeholders, hashes and ids that look like keys but aren't secrets
NEG = [
    "aws_access_key_id = AKIAIOSFODNN7EXAMPLE\naws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n",
    "OPENAI_API_KEY=sk-...your-key-here...\n",
    "export ANTHROPIC_API_KEY=<your key>\n",
    "STRIPE_SECRET_KEY=sk_test_" + "4eC39HqLyjWDarjtT1zdp7dc\n",
    "Use a token like ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx in the header.\n",
    f"commit {rnd(HEX, 40)}\nAuthor: CI <ci@example.com>\n\n    bump deps\n",
    f"request_id={rnd(HEX, 8)}-{rnd(HEX, 4)}-{rnd(HEX, 4)}-{rnd(HEX, 4)}-{rnd(HEX, 12)} status=200 ms=41\n",
    f"sha256:{rnd(HEX, 64)}  dist/app.tar.gz\n",
    "SLACK_BOT_TOKEN=${SLACK_BOT_TOKEN}\n",
    "password: \"\"  # set in the vault, never here\n",
    "mysql -h 127.0.0.1 -u root -p\nEnter password:\n",
    "The README says to run `redis-cli AUTH <password>` first.\n",
    f"image: ghcr.io/acme/api@sha256:{rnd(HEX, 64)}\n",
    "GOOGLE_API_KEY=  # leave empty for local dev\n",
    f"etag: W/\"{rnd(HEX, 32)}\"\n",
]
for t in NEG:
    add("negative", t, [])

R.shuffle(items)
for i, it in enumerate(items, 1):
    it["id"] = f"h7-{i:03d}"
    print(json.dumps(it))
