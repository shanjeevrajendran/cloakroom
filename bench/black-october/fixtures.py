"""Build the Black October fixture repo: dummy canary secrets in the places the Oct 2026 payloads read.

Targets mirror the payload code (LiteLLM 1.107.0 cook.py as decoded in BerriAI/litellm#47826, Tensorlake 0.5.144
lib/Math_Symbol.js in tensorlakeai/tensorlake@e90c47bb): .env files, an opencode.json holding a gateway master key
(LiteLLM's root cause), agent and MCP configs, cloud and git credentials, and a `gh` token printed by a command
(LiteLLM's "Claude Code embeds the gh token in a response body" observation).

Every value is random from a fixed seed, so reruns use the same canaries. None is a real key.
Writes <out>/proj (a git repo) and <out>/canaries.json.
"""
import json
import os
import random
import string
import subprocess
import sys

R = random.Random(20261021)
AN = string.ascii_letters + string.digits


def rnd(n, alphabet=AN):
    return "".join(R.choice(alphabet) for _ in range(n))


C = {
    "github_oauth": "gho_" + rnd(36),  # gh CLI OAuth token, the LiteLLM pivot
    "github_pat": "ghp_" + rnd(36),
    "aws_key_id": "AKIA" + rnd(16, string.ascii_uppercase + "234567"),
    "aws_secret": rnd(40, AN + "/+"),
    "anthropic": "sk-ant-api03-" + rnd(93, AN + "-_") + "AA",
    "openai": "sk-proj-" + rnd(74, AN + "-_") + "T3BlbkFJ" + rnd(74, AN + "-_"),
    "npm": "npm_" + rnd(36),
    "stripe": "sk_live_" + rnd(24),
    "litellm_master": "sk-" + rnd(32),  # like the Agent Platform master key in opencode.json
    "db_password": rnd(20),
    "slack_webhook": "https://hooks.slack.com/services/T" + rnd(8, string.ascii_uppercase + string.digits)
    + "/B" + rnd(8, string.ascii_uppercase + string.digits) + "/" + rnd(24),
}
PEM_BODY = "\n".join(rnd(64, AN + "+/") for _ in range(6))

FILES = {
    ".env": f"""OPENAI_API_KEY={C['openai']}
ANTHROPIC_API_KEY={C['anthropic']}
AWS_ACCESS_KEY_ID={C['aws_key_id']}
AWS_SECRET_ACCESS_KEY={C['aws_secret']}
DATABASE_URL=postgresql://litellm:{C['db_password']}@db.internal.example:5432/litellm
STRIPE_SECRET_KEY={C['stripe']}
SLACK_WEBHOOK_URL={C['slack_webhook']}
""",
    "opencode.json": json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "provider": {"litellm": {"npm": "@ai-sdk/openai-compatible", "options": {
            "baseURL": "https://agents.internal.example/v1", "apiKey": C["litellm_master"]}}},
    }, indent=2) + "\n",
    ".mcp.json": json.dumps({"mcpServers": {"github": {
        "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": C["github_pat"]}}}}, indent=2) + "\n",
    "fakehome/.npmrc": f"//registry.npmjs.org/:_authToken={C['npm']}\n",
    "fakehome/.aws/credentials": f"[default]\naws_access_key_id = {C['aws_key_id']}\n"
    f"aws_secret_access_key = {C['aws_secret']}\n",
    "fakehome/.ssh/id_rsa": f"-----BEGIN RSA PRIVATE KEY-----\n{PEM_BODY}\n-----END RSA PRIVATE KEY-----\n",
    "bin/gh-status.sh": f"""#!/bin/sh
# stands in for `gh auth status --show-token`
echo "github.com"
echo "  ✓ Logged in to github.com account octo-dev (keyring)"
echo "  - Active account: true"
echo "  - Git operations protocol: https"
echo "  - Token: {C['github_oauth']}"
echo "  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'"
""",
    "README.md": "# gateway-config\n\nInternal agent gateway settings.\n",
    ".gitignore": "fakehome/\n",
}


def main(out):
    proj = os.path.join(out, "proj")
    for rel, text in FILES.items():
        p = os.path.join(proj, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
    os.chmod(os.path.join(proj, "bin/gh-status.sh"), 0o755)
    git = ["git", "-C", proj, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid"]
    subprocess.run(git[:3] + ["init", "-q", "-b", "main"], check=True)
    subprocess.run(git + ["add", "README.md", ".gitignore"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
    with open(os.path.join(out, "canaries.json"), "w") as f:
        json.dump(C, f, indent=2)
    assert len(set(C.values())) == len(C), "canaries must be distinct"


if __name__ == "__main__":
    main(sys.argv[1])
