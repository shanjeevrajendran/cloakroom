# Cloakroom for Claude Code

Cloakroom is a privacy layer for Claude Code. Names, emails, phone numbers, addresses, API keys and other secrets
in the files Claude reads and the commands it runs are swapped for placeholders such as `PII_PERSON_1` before the
model sees them. When Claude writes a file or runs a command on your machine, the real values are put back
locally. A prompt that contains a secret is blocked, with a hint for sending it anyway (`cr!`).

Full documentation, benchmark results and source: https://github.com/shanjeevrajendran/cloakroom

## Get started

You need Claude Code, installed and signed in. Then:

1. **Open a terminal.** Mac: press Cmd+Space, type "Terminal", press Enter. Windows: open Start, type
   "PowerShell", press Enter. Linux: press Ctrl+Alt+T. Type `claude` and press Enter.
2. **Add Cloakroom.** Inside Claude Code, type these two lines, one at a time. If it asks where to install, pick the
   option for all your projects (user scope).

   ```
   /plugin marketplace add shanjeevrajendran/cloakroom
   /plugin install cloakroom@cloakroom
   ```

3. **Restart Claude Code.** Type `/exit`, then `claude` again. Cloakroom now installs itself. Keys and card numbers
   are covered in about a minute; names and addresses a few minutes later, once the detection model has downloaded
   (about 1 GB on a Mac, up to about 4 GB elsewhere). Claude's tool calls are held back until then.
4. **Check it.** Open a new terminal window and run `cloakroom doctor` (every line should say ok), then
   `cloakroom dashboard` to see your alerts in the browser.

What you need first: on Windows, nothing. On Mac and Linux, uv, or Python 3.10 or newer (on Ubuntu and Debian, also
`python3-venv`). If Cloakroom says it has nothing to install itself with, run
`curl -LsSf https://astral.sh/uv/install.sh | sh` in a terminal and restart Claude Code.

If `cloakroom` isn't found, open a new terminal: the program lives in `~/.local/bin` (on Windows,
`%USERPROFILE%\.local\bin`). Setup progress is written to `~/.cloakroom/bootstrap.log`.

## What this plugin runs

- **Hooks** on SessionStart, UserPromptSubmit, PreToolUse and PostToolUse. Each hook calls the `cloakroom`
  program installed on your machine and passes it the hook's JSON. Detection runs locally: rules plus a small
  detection model, and local OCR (RapidOCR) for scanned PDFs and photos.
- **A first-start installer** (`scripts/bootstrap.sh`, `scripts/bootstrap.ps1` on Windows). When `cloakroom` isn't
  installed yet, it installs the pinned release of the `cloakroom` Python package from PyPI, using uv, else pipx,
  else a private virtual environment in `~/.cloakroom/venv`. On Windows it fetches uv first when neither is there.
  Until that finishes, tool calls are held back.
- **A local service** on `127.0.0.1` (port 37787) that keeps the model loaded between checks.

## Privacy

Everything runs on your machine. Cloakroom reads your prompts, the output of Claude's tool calls and the files
Claude writes, so it can mask and restore values. It sends none of it anywhere; the details are below. To remove
everything: `/plugin uninstall cloakroom`, uninstall the program (`uv tool uninstall cloakroom` or
`pipx uninstall cloakroom`), delete `~/.cloakroom`, and delete the `cloakroom` / `vault-key` entry from your
system's secure store. How masking works and what it does not protect against:
[How it works](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-HOW-IT-WORKS.md#threat-model).
Security reports: [SECURITY.md](https://github.com/shanjeevrajendran/cloakroom/blob/main/SECURITY.md).

### What it fetches and sends

- **Fetches once:** the `cloakroom` package and its dependencies from PyPI, and the PII-Tracer detection model
  (`perplexity-ai/PII-Tracer`, a pinned revision) from Hugging Face. On Windows without uv or pipx, it first
  fetches uv from its maker's installer (astral.sh). On machines without a GPU it builds a faster
  CPU copy of the model locally. The OCR models for scanned PDFs and photos ship inside the package, so OCR
  downloads nothing and runs on your CPU.
- **Sends nothing.** File contents, prompts and tool output never leave your machine through Cloakroom. The only
  network calls are the downloads above.

### What it stores

- An AES-GCM encrypted vault of the real values for each session in `~/.cloakroom`, with the key in your system's
  secure store (Keychain, Windows Credential Manager, Secret Service).
- An activity log without values, and your labels and rules (encrypted) for the local dashboard
  (`cloakroom dashboard`).

## Requirements and removal

Windows needs nothing installed first; macOS and Linux need uv, pipx or Python 3.10+ (on Ubuntu/Debian:
`python3-venv`). About 1 GB on a Mac, up to about 4 GB elsewhere. To remove it: `/plugin uninstall cloakroom`,
then `uv tool uninstall cloakroom` (or delete `~/.cloakroom`).

License: Apache-2.0. Made by Shanjeev Rajendran.
