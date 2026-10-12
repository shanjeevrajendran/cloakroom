# Cloakroom for Claude Code

Cloakroom is a privacy layer for Claude Code. Names, emails, phone numbers, addresses, API keys and other secrets
in the files Claude reads and the commands it runs are swapped for placeholders such as `PII_PERSON_1` before the
model sees them. When Claude writes a file or runs a command on your machine, the real values are put back
locally. A prompt that contains a secret is blocked, with a hint for sending it anyway (`cr!`).

Full documentation, benchmark results and source: https://github.com/shanjeevrajendran/cloakroom

## Install

```
/plugin install cloakroom --marketplace shanjeevrajendran/cloakroom-plugin
```

Then restart Claude Code. Cloakroom installs itself on first start (a few minutes; progress in
`~/.cloakroom/bootstrap.log`).

## What this plugin runs

- **Hooks** on SessionStart, UserPromptSubmit, PreToolUse and PostToolUse. Each hook calls the `cloakroom`
  program installed on your machine and passes it the hook's JSON. Detection runs locally: rules plus a small
  detection model.
- **A first-start installer** (`scripts/bootstrap.sh`, `scripts/bootstrap.ps1` on Windows). When `cloakroom` isn't
  installed yet, it installs the pinned release of the `cloakroom` Python package from PyPI, using uv, else pipx,
  else a private virtual environment in `~/.cloakroom/venv`. Until that finishes, tool calls are held back.
- **A local service** on `127.0.0.1` (port 37787) that keeps the model loaded between checks.

## What it fetches and sends

- **Fetches once:** the `cloakroom` package and its dependencies from PyPI, and the PII-Tracer detection model
  (a pinned revision) from Hugging Face. On machines without a GPU it builds a faster CPU copy of the model locally.
- **Sends nothing.** File contents, prompts and tool output never leave your machine through Cloakroom. The only
  network calls are the downloads above.

## What it stores

- An encrypted vault of the real values for each session in `~/.cloakroom`, with the key in your system's secure
  store (Keychain, Windows Credential Manager, Secret Service).
- An activity log without values, and your labels and rules (encrypted) for the local dashboard
  (`cloakroom dashboard`).

## Requirements and removal

macOS, Windows or Linux with uv, pipx or Python 3.10+ (on Ubuntu/Debian: `python3-venv`). About 1 GB on a Mac, up to
about 4 GB elsewhere. To remove it: `/plugin uninstall cloakroom`, then `uv tool uninstall cloakroom` (or delete
`~/.cloakroom`).

License: Apache-2.0. Made by Shanjeev Rajendran.
