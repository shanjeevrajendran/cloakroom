"""Holdout corpus: new formats written after the engine was tuned, never used for tuning.

    python bench/gen_holdout.py > bench/holdout.jsonl
"""
import base64
import json
import random
import string

R = random.Random(777)
AZ09 = string.ascii_letters + string.digits
NAMES = ["Beatriz Okonkwo", "Henrik Dahl", "Yuki Nakamura", "Samuel Achterberg", "Leila Farahani", "Cormac Doyle",
         "Nadia Petrov", "Gabriel Esposito", "Thandiwe Nkosi", "Viktor Halvorsen"]
items = []


def add(cat, text, planted):
    items.append({"id": f"{cat}-{len(items) + 1:03d}", "category": cat, "text": text, "planted": planted})


for n in NAMES:
    first, last = n.split()
    e = f"{first[0].lower()}{last.lower()}@{R.choice(['quillfield.com', 'marrowgate.io', 'tessellate.org'])}"
    ph = f"+44 20 7{R.randint(100, 999)} {R.randint(1000, 9999)}"
    add("md_meeting", f"## 1:1 notes\n- Spoke with **{n}** about the migration.\n- Reach her at [{e}](mailto:{e}) or {ph}.\n", [n, e, ph])
    tok = "ghp_" + "".join(R.choice(AZ09) for _ in range(36))
    add("curl_cmd", f"$ curl -H 'Authorization: Bearer {tok}' https://api.github.com/user/repos", [tok])
    pw = "".join(R.choice(AZ09) for _ in range(14))
    add("yaml_cfg", f"database:\n  host: db.internal\n  user: reporting\n  password: {pw}\n  owner: {n}\n", [pw, n])
    add("stack_trace", f'Traceback (most recent call last):\n  File "/Users/{first.lower()}{last.lower()[0]}/proj/app.py", line 12\n'
        f"ValueError: customer {n} <{e}> has no billing address\n", [n, e])
    raw = "".join(R.choice(AZ09) for _ in range(24))
    k8s = base64.b64encode(raw.encode()).decode()
    add("sql_insert", f"INSERT INTO customers (name, email, ssn) VALUES ('{n}', '{e}', '{R.randint(100, 665)}-{R.randint(10, 99)}-{R.randint(1000, 9999)}');", [n, e])
    add("dockerfile", f"FROM python:3.12-slim\nENV STRIPE_KEY=sk_live_{''.join(R.choice(AZ09) for _ in range(24))}\nCMD [\"python\", \"app.py\"]\n", [])
    items[-1]["planted"] = [items[-1]["text"].split("STRIPE_KEY=")[1].split()[0]]

NEGS = [
    "## Changelog\n- 3.2.0: drop Python 3.8; bump pydantic to 2.9 (thanks @octocat)\n",
    "kubectl get pods -n staging\nNAME                     READY   STATUS    RESTARTS   AGE\napi-7d9f8c6b5-x2k4p      1/1     Running   0          3d\n",
    "export DATABASE_URL=${DATABASE_URL:-postgres://localhost:5432/dev}\n",
    "const token = await getToken(); // fetched at runtime from the vault\n",
    "Contact sales@acme-widgets.com or call 1-800-555-0199 for enterprise pricing.\n",
    "Fixture: users.json -> [{\"name\": \"Jane Doe\", \"email\": \"jane.doe@example.org\"}]\n",
    "sha512: cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e\n",
    "Listening on http://0.0.0.0:8000 (Press CTRL+C to quit)\nINFO: 192.168.1.20:51234 - \"GET /health HTTP/1.1\" 200 OK\n",
    "Thanks to Linus Torvalds and the Git community for the original design.\n",
    "AWS docs sample: aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n",
    "npm WARN deprecated inflight@1.0.6: This module is not supported, and leaks memory.\n",
    "Pull request #812 merged by github-actions[bot] on 2026-09-14.\n",
    "Set PASSWORD in your .env file (see .env.sample). Never commit it.\n",
    "Office hours: Tue 2-4pm, room 3.14. Bring your laptop charger.\n",
    "BEGIN TRANSACTION; UPDATE inventory SET qty = qty - 1 WHERE sku = 'WID-2201'; COMMIT;\n",
]
for t in NEGS:
    add("negative", t, [])

for it in items:
    print(json.dumps(it))
