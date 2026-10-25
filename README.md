<div align="center">

<img src="https://raw.githubusercontent.com/shanjeevrajendran/cloakroom/main/assets/hero.svg" alt="Cloakroom, a privacy layer for AI coding agents (Claude Code, Codex CLI, Gemini CLI): names, emails, addresses and API keys are swapped for placeholders before the model sees them." width="880">

**Cloakroom is free and open source. It masks API keys, secrets and personal data (PII) on your machine before your AI coding agent sees them. Works with Claude Code, Codex CLI and Gemini CLI.**

<p>
  <a href="https://www.youtube.com/shorts/U9lN4emgeE0">▶ 40-second demo</a> ·
  <a href="#get-started">Get started</a> ·
  <a href="#when-cloakroom-blocks-your-prompt">Blocked prompt?</a> ·
  <a href="#results">Results</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#extra-protections">Extra protections</a> ·
  <a href="#dashboard">Dashboard</a> ·
  <a href="#faq">FAQ</a> ·
  <a href="https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-LIMITS.md">Known limits</a>
</p>

<p>
  <a href="https://pypi.org/project/cloakroom/"><img src="https://img.shields.io/pypi/v/cloakroom" alt="PyPI version"></a>
  <a href="https://pypi.org/project/cloakroom/"><img src="https://img.shields.io/pypi/pyversions/cloakroom" alt="Python versions"></a>
  <a href="https://github.com/shanjeevrajendran/cloakroom/actions/workflows/test.yml"><img src="https://github.com/shanjeevrajendran/cloakroom/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/shanjeevrajendran/cloakroom/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License: Apache-2.0"></a>
  <img src="https://img.shields.io/badge/Claude%20Code-plugin-d97757" alt="Claude Code plugin">
</p>

</div>

**Cloakroom swaps your secrets and personal data for placeholders before Claude sees them, and puts the real values
back in the files Claude writes.**

The only Claude Code privacy tool that catches personal data, masks instead of blocking, and learns from your corrections.

Runs on your machine, sets itself up on first start, works on Mac, Windows and Linux. Free and open source. Young:
expect rough edges.

New in 0.7, it also protects other coding agents (Mac and Linux):

- **OpenAI's Codex CLI:** run `cloakroom install codex` ([Codex](#codex)).
- **Google's Gemini CLI:** run `cloakroom install gemini` ([Gemini CLI](#gemini-cli)).

**Hand Claude the files you'd never paste into a chat:**

- **Bank statements** (PDF, scan or CSV): "sort a year of spending by category." Claude sees amounts and merchants, not your name, address or account number.
- **Support tickets:** "draft replies to this week's tickets." Claude writes to a stand-in name (`PII_PERSON_1`);
  the drafts saved on your disk have the real names.
- **Medical or HR records:** names, birth dates, addresses and ID numbers are masked, so Claude can summarise or tidy a record without knowing whose it is.
- **`.env` files and logs:** Claude fixes the config without ever seeing the password or API key.
- **Disguised copies:** a key or email that a command hashed, hex-encoded or reversed is still masked
  ([what's covered](#disguised-copies)).
- **Finance work:** bank and card numbers are masked, calls that move money wait for your OK, and `cloakroom report`
  with the tamper-evident log is your audit trail ([for finance teams](#for-finance-teams)).
- **Scans and photos of documents:** read on your machine (no upload) and masked like any other file.
- **Learns from you:** mark a false alarm once in the dashboard and a rule stops it next time.

<div align="center">
  <img src="https://raw.githubusercontent.com/shanjeevrajendran/cloakroom/main/assets/demo.gif" alt="Claude Code reads a support ticket and .env through Cloakroom: the model sees PII_PERSON_1 and PII_SECRET_1, and reply.md on disk gets the real customer name back" width="820">
  <br><sub>Claude writes the reply. The customer's name never left your machine.</sub>
</div>

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
   It also updates itself when the plugin updates (an install you made by hand is left alone).
   On a Mac, if it asks to use the "cloakroom" item in your Keychain, click Always Allow (it only lets Cloakroom read its own key).
4. **Check it.** Open a new terminal window and run:

   ```bash
   cloakroom doctor       # every line should say ok
   cloakroom dashboard    # opens your alerts in the browser
   ```

What you need first:

- **Windows, Mac and Linux:** nothing. When neither uv nor pipx is there, Cloakroom fetches uv (a pinned version,
  checked against its SHA-256 before it runs), and uv brings its own Python. That needs `curl` or `wget`, which
  macOS and most Linux systems have. If a Linux machine has neither, Cloakroom tells you the steps:
  `sudo apt install curl` (Ubuntu/Debian) or `sudo dnf install curl` (Fedora/RHEL), then restart Claude Code.
- **Intel Mac:** rules-only protection (keys, cards, IDs). PyTorch and ONNX Runtime publish no Intel Mac builds, so
  the detection model for names and addresses can't run there.

If something looks wrong:

- **`cloakroom` not found:** open a new terminal. The program lives in `~/.local/bin` (on Windows,
  `%USERPROFILE%\.local\bin`); the installer adds that folder to your shell profile (`~/.zshenv` for zsh,
  `~/.bashrc` and `~/.profile` or `~/.bash_profile` for bash) or, on Windows, to your user PATH.
- **Still setting up, or it failed:** progress, and numbered steps when something needs you, are written to
  `~/.cloakroom/bootstrap.log`. More in
  [Setup](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-SETUP.md).

Tested from clean installs on macOS, Windows and Ubuntu.

Prefer to install by hand: `uv tool install "cloakroom[model,torch,ocr]"` (Python 3.10+; the part in brackets adds
the detection model and OCR), then add the plugin as above.

## How it works

```
  Claude Code reads a file or runs a tool
        │   "Nadia Petrov called about…"    sk_live_51Hx9Q…
        ▼
  ┌────────────────────────────────────────────┐
  │ Cloakroom  (on your machine)               │
  │   rules + local model  ->  find the values │
  │   encrypted vault      ->  remember them   │
  └────────────────────────────────────────────┘
        │   "PII_PERSON_1 called about…"    PII_SECRET_1
        ▼
  the model works with placeholders only
        │   Write reply.md: "Hi PII_PERSON_1,"
        ▼
  Cloakroom puts the real value back, locally
        │
        ▼
  reply.md on your disk: "Hi Nadia Petrov,"
```

Details: [docs-HOW-IT-WORKS.md](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-HOW-IT-WORKS.md).

## Extra protections

Masking is the core. These come with it, on by default, and need nothing from you.

**Real values don't leave your machine**

- **Commands that send data are held:** a command that would send a known secret off your machine (curl, wget,
  ssh, scp, rsync, nc, git push with credentials, a literal token in an `Authorization` header) is held, and the
  message names what it found by placeholder only. If you meant it, type `cr: allow`, then ask Claude to run the
  same command again: it passes once.
- **Network one-liners count too:** `python -c`, `node -e`, `ruby -e`, `perl -e`, `php -r`, `deno eval` and
  `bash -c` that can open a connection are treated like curl.
- **Encoded copies count:** a secret Cloakroom has seen is also caught base64-encoded, URL-encoded or JSON-escaped.
- **Disguised copies count:** that secret is also caught hashed, as hex or reversed ([details](#disguised-copies)).
- **Web, MCP and subagent calls carry placeholders only:** a web fetch, an MCP tool call or a prompt to a subagent
  that holds a real value is stopped before it runs.
- **Pictures and PDFs become masked text:** PDFs, scans, photos and images returned by MCP tools are read on your
  machine and Claude gets masked text, never the file or the picture. Without OCR an image is held back, with a
  one-line reason.

<a id="disguised-copies"></a>**Disguised copies**

Once Cloakroom has seen a key, password, card or account number, or an email, it also looks for copies a command
changed on the way. Each copy gets its own placeholder, and the exact copy is put back in files Claude writes.

- **Covered:** MD5, SHA-1 and SHA-256 hashes of a key or an email; hex (`6b39...`, `0x...`, `6b 39 ...`,
  `\x6b...`); a key or password of 12+ characters written backwards; pairs like `user:password` or
  `DB_PASSWORD=...` in hex or URL encoding; a card or account number's last 4 digits right after "ending in",
  "last 4" or `****`; an email's username on its own when it can't be an ordinary word (`r.okafor77`, not `admin`).
- **Not covered:** salted or keyed hashes (bcrypt, HMAC), other hashes, a name in different capitals, initials,
  a last 4 with no word pointing at it, and anything Cloakroom hasn't seen in plain form first.

**Keys stay out of git**

- **Files git would commit:** when Claude writes a file, a key or password that would end up in a tracked file is
  refused, and Claude is told to use `.env` instead. Ignored files and `.env` work as before.
- **Commits and pushes:** before Claude runs `git commit` or `git push`, Cloakroom reads what would be committed or
  pushed. A key or password there holds the command, naming the file and placeholder; `cr: allow` lets it run once.
- **Agent config files:** a live token written into `.mcp.json`, `.claude/settings.json`, `.cursor/mcp.json` or a
  copy of `claude_desktop_config.json` in a repo is refused, and Claude is told to use an environment reference
  such as `${GITHUB_TOKEN}`.

**You stay in control, and can show it**

- **Claude can't turn Cloakroom off:** its settings change only from the dashboard or when you type `cr: pause` or
  `cr: resume`.
- **A log you can check and export:** each line of the activity log carries the hash of the line before it, so an
  edited or removed line shows up. `cloakroom report` (or Export on the dashboard) gives counts per day and session,
  without values.
- **Placeholders can't be guessed:** each one ends in a short tag only your machine can make
  (`PII_SECRET_1_k7q2ab`), and only placeholders issued in this session get their real value back. A file that
  names `PII_SECRET_1` to fish for a key gets nothing; after three such tries in a session you see one line.
- **Files Claude wrote stay masked:** when a real value is put back into a file, Cloakroom remembers where (never the
  value), so reading that file again later, even in a new session, shows the placeholder.
- **Old transcripts can be cleaned:** `cloakroom clean-history` finds keys and passwords already sitting in old
  Claude Code transcripts and your prompt history, and replaces them on your yes, with a backup you delete.

<a id="for-finance-teams"></a>**For finance teams**

- **Bank numbers are masked:** US routing numbers (the ABA checksum plus a word or field like "routing" or "ABA")
  and bank account numbers (next to "account number", "acct", "checking account", "DDA", in a CSV column of that
  name, a line of an ACH payment file, or beside a routing number in the same record) become `PII_BANK_1_k7q2ab`.
  Digits with no such word stay as they are, so order numbers, phone numbers and ZIP+4 codes are left alone.
- **Calls that move money are held:** a write to a payment, payout or transfer API (Stripe, Plaid, Dwolla, Wise,
  Adyen, PayPal, Square, Increase, Modern Treasury, Column) from curl, PowerShell or a payment CLI, or an MCP tool
  named for it (`send_money`, `create_payout`, `initiate_transfer`), waits until you type `cr: allow`; then it runs
  once. Reads such as a balance check pass. Add your own hosts and tools in `~/.cloakroom/config.json` with
  `"money_hosts": ["payments.example.com/api/wires"]` and `"money_tools": ["disburse"]`; turn the hold off with
  `"money_hold": false`.
- **The audit trail:** every hold and every `cr: allow` lands in the same tamper-evident log (above), so
  `cloakroom report` shows them per day and session.

<details>
<summary><b>Commands and limits</b></summary>

- **`cloakroom report [--since 2026-10-01] [--format csv|json]`:** counts per day and session by data kind and action
  (masked, withheld, held, allowed once, denied), with paths reduced to their extension and top folder. It starts
  with what it covers and what it can't: a miss leaves no line, and other agents, machines without Cloakroom and
  prompts sent with `cr!` are not in it. A "Checks skipped" section counts the calls let through without a full
  check (too large, too slow, a git error, the model not ready), by check and reason; you're also told about each
  reason once per session when it happens.
- **`cloakroom report --verify`:** checks the log's hash chain and prints the head hash. Keep that somewhere else:
  anyone who rewrites every later line can rebuild the chain. The log keeps `"log_months"` months (default 12).
- **`cloakroom clean-history [--dry-run]`:** the first run only counts, per project. Files changed in the last ten
  minutes (an open session) are skipped.
- **Commit and push check:** it reads the staged change (for `git push`, the commits no remote has yet) within two
  seconds; a larger or slower one runs unchecked. Repos that commit sample keys need `"allow_secret_paths"`.

</details>

## Dashboard

Where your labels become rules: false alarms fall as you label them, and the detection model itself never changes.

`cloakroom dashboard` opens a page served by Cloakroom itself on 127.0.0.1, signed in with a one-time link.

<div align="center">
  <img src="https://raw.githubusercontent.com/shanjeevrajendran/cloakroom/main/assets/dashboard-preview.png" alt="The Cloakroom dashboard: health status, values kept from the model, the alert feed with labels, and the learning panel" width="820">
</div>

- **Alerts:** every masked, withheld or blocked call, with the kinds found and how long the check took. Open one
  to see its placeholders; the real values show only when you click Reveal.
- **Your labels:** mark an alert *correct*, a *false alarm*, or type what it *missed*.
- **Rules from your labels:** a false alarm suggests "never mask this", a miss suggests "always mask this". You
  approve each one, or later let rules apply by themselves after a replay test against your past labels; rules that
  would unmask a key, password, card or ID number always wait for you. Undo puts everything back. The detection model
  never changes. Details: [the handbook](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-HANDBOOK.md).
- **Private by design:** the activity log holds no values or text; labels and rules are encrypted like the vault.

## When Cloakroom blocks your prompt

Claude Code lets Cloakroom block a prompt but not rewrite it, so a prompt holding a key, an email, a phone, a card or
an ID number is stopped. Names alone and plain dates go through. The block message says what it found, and how to
get past it:

| Type | What happens |
|---|---|
| `cr!` at the start of your prompt | that one prompt is sent as it is |
| `cr: pause 10m` | prompts aren't blocked for 10 minutes (any number, up to 240); each shows the time left |
| `cr: resume` | checks are back on |

Files and command output stay masked the whole time. Only you can do this: Claude never sees these commands and
can't change Cloakroom's settings. Usually better still: put the detail in a file and ask Claude to read it. It gets
masked on the way in and the real values come back when Claude writes.

## Why Cloakroom

Claude Code reads whatever your task touches: `.env` files, logs, customer exports, support tickets. All of it
goes to the model. Secret scanners catch keys with a known shape, but a customer's name in a ticket or an address
in a log looks like ordinary text to them.

- **Catches personal data, not just keys.** A small detection model on your machine, plus rules, finds names,
  emails, phone numbers and addresses that have no fixed shape. On fresh test sets it let through 0 of 1,005 of
  them; the other tools we tested let through 119 to 992 ([Results](#results)).
- **Claude keeps working.** Placeholders stay the same for the whole session, and the real values are put back
  when Claude writes a file or runs a command locally.
- **Runs on your machine.** Detection, OCR, the encrypted vault (where the real values wait) and the put-back are
  all local. The only network call is the one-time model download (the OCR models ship inside the package).
- **Mac, Windows and Linux.** One install on all three, with the vault key in each system's own secure store:
  Keychain on macOS; Credential Manager on Windows (runs in PowerShell or Git Bash, tested on Windows Server);
  Secret Service on Linux (tested on Ubuntu 24.04, plus automatic tests on every change).
- **No graphics card needed.** Each file or command is checked in a fraction of a second on an ordinary laptop
  (`cloakroom doctor` prints your numbers).
- **Catches encoded output.** If a command prints data in encoded form (`base64`, hex dumps from `xxd` or `od`),
  Cloakroom decodes it and checks it too.
- **If it can't check, it doesn't let things through.** When Cloakroom isn't ready, Claude's file reads and
  commands wait instead of passing unchecked. Your prompts still get a quick rules check, with a warning, so you're
  never locked out.

## Results

- **Good at:** personal data (names, emails, phone numbers, addresses) and secrets in odd places. On fresh test
  sets it let through 0 of 1,005 personal details; the other Claude Code tools we tested let through 119 to 992.
  It masks, so Claude keeps working; one other tool blocks the whole step instead.
- **Weaker at:** False alarms. Cloakroom is tuned to mask rather than risk a leak, so it masks more harmless text
  than secret-only scanners; Claude still gets the job done with a placeholder. Label a false alarm in the dashboard
  and a rule stops it next time.
- **What a false alarm costs:** Claude sees a placeholder where it needed the real text. Nothing leaks.

Every number comes from a set run once before any tuning on it unless it says otherwise. All tools at default
settings, offline, tested 12 to 14 October 2026 (Cloakroom's rows rerun 24 October for 0.6.2). maisecrets'
maintainers reproduced our numbers and pointed out where the older sets were unfair to them (keys in formats vendors
don't issue, and the `regions` setting); the key-format set and the setting below are the fix. The maintainers of
the compared tools were contacted before publishing. Method, versions and every run:
[bench/RESULTS.md](https://github.com/shanjeevrajendran/cloakroom/blob/main/bench/RESULTS.md).

**Made-up agent traffic** (five sets written by a different model: chats, logs, config files, spreadsheets, support
tickets): 1,339 key and personal values (the rest are in RESULTS.md) and 200 safe look-alikes. Fewer is better.
These sets have been seen by now, so Cloakroom's second row is not an honest score; the first row is its run before
any tuning on them.

| Tool | Personal data leaked (of 1,005) | Keys, passwords, cards, IDs leaked (of 334) | False alarms (of 200 safe samples) |
|---|---|---|---|
| **Cloakroom, first run** | **0** | 4 | 60 |
| Cloakroom today (has seen these sets, so not a fair score) | 1 | **2** | 20 |
| sensitive-canary (blocks the call instead of masking) | 119 | 40 | 16 |
| maisecrets (`"regions": ["us", "uk", "in"]`) | 547 | 54 | 6 |
| claude-code-redact, PII on | 607 | 101 | 31 |
| redact-hook | 817-822 | 143-157 | 35-40 |
| claude-code-redact, secrets only | 992 | 179 | **2** |

Some of these tools only aim at secrets, so their personal-data column shows scope as much as quality. Cloakroom's
one leak today: a date of birth in a dating-profile export.

**Keys in the formats vendors really issue** (a new set written after the first comparison: 15 key types,
passwords with no fixed shape, encoded emails; 77 values, 15 look-alikes):

| Tool | Leaked (of 77) | False alarms (of 15) |
|---|---|---|
| **Cloakroom** | **0** | 2 |
| maisecrets | 3 | **1** |
| sensitive-canary | 14 | 3 |
| redact-hook (default / aggressive) | 15 | 5 |
| claude-code-redact (PII on / secrets only) | 17 | 2 / **1** |

**Real passwords and keys from public code** ([Samsung CredData](https://github.com/Samsung/CredData)): 14,471
real secrets and 15,422 harmless look-alikes, each line checked alone, the way a tool output reaches Claude. One
repository is almost all cipher test vectors (sample keys for testing encryption code), which maisecrets' keyword
rule is built to catch: first number without it, in brackets the whole set.

| Tool | Real secrets caught (of every 100), higher is better | False alarms (of every 100 harmless look-alikes), lower is better |
|---|---|---|
| **Cloakroom** | **73** (65) | 19 (22) |
| maisecrets | 58 (**68**) | 7 (6) |
| claude-code-redact | 36 (31) | 8 (7) |
| sensitive-canary (blocks the call instead of masking) | 30 (27) | 21 (18) |
| redact-hook, default | 27 (19) | **5** (**4**) |
| redact-hook, aggressive | 36 (26) | 12 (20) |

Without the test-vector repository, Cloakroom catches the most; with it, maisecrets does, and maisecrets is the most
precise either way (of what it masks, 86 to 91 in 100 are real secrets, against Cloakroom's 73 to 74). Cloakroom
masks far more harmless look-alikes (3,457 against maisecrets' 997 on the whole set, down from 3,903 before 0.6.2):
false alarms are Cloakroom's main open problem ([precision, recall and F1 for every tool](https://github.com/shanjeevrajendran/cloakroom/blob/main/bench/RESULTS.md)).

**On public datasets we didn't write** (run once; details in
[bench/public/RESULTS.md](https://github.com/shanjeevrajendran/cloakroom/blob/main/bench/public/RESULTS.md)):

| Dataset | Personal details found (of every 100) | Masks that were not labelled personal data (of every 100) |
|---|---|---|
| [TAB](https://github.com/NorskRegnesentral/text-anonymization-benchmark), real court rulings from the European Court of Human Rights | 95 | 19 |
| [Gretel](https://huggingface.co/datasets/gretelai/synthetic_pii_finance_multilingual), made-up finance documents | 85 | 48 |

On Gretel, over half of those extra masks are number strings Cloakroom treats as account or reference numbers; the
rest are mostly names and addresses Gretel leaves unlabelled.

## Codex

Cloakroom also works with OpenAI's Codex CLI, on Mac and Linux (tested with Codex 0.164).

1. Install the program by hand: `uv tool install "cloakroom[model,torch,ocr]"`.
2. Run `cloakroom install codex`. It adds four hooks to `~/.codex/hooks.json` and leaves your own hooks alone.
3. Start Codex (`codex`), type `/hooks`, open each of the four Cloakroom hooks and trust it. Until you do, Codex
   skips them, and `codex exec` gives no warning, so nothing is masked. `cloakroom doctor` shows whether they're
   installed.

What works as in Claude Code: shell output and MCP results are masked before the model sees them, placeholders in
shell commands and patches get their real values back on your machine, real values are kept out of MCP calls, and a
prompt holding a secret is stopped with what to do next. What's different:

- Codex can't swap a tool's output in place, so the masked output reaches the model as a "blocked" tool result
  that holds the masked text.
- Codex hooks can't ask you. A command that would send placeholders off the machine, or one Cloakroom doesn't
  know, is stopped instead of asked about.
- **Open gap:** a command that runs longer than Codex waits (10 seconds by default), such as a dev server, a watch
  or a slow build, hands its first output to the model while it is still running, and Codex runs no hook on that
  output. Cloakroom can't tell such a command apart beforehand, so that output is not masked. Until this is fixed,
  don't let Codex start long-running commands that print secrets or personal data.

To remove it: `cloakroom uninstall codex`. More in
[Known limits](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-LIMITS.md#codex).

## Gemini CLI

Cloakroom also works with Google's Gemini CLI, on Mac and Linux (built against Gemini CLI 0.65).

1. Install the program by hand: `uv tool install "cloakroom[model,torch,ocr]"`.
2. Run `cloakroom install gemini`. It writes one thing: a Gemini extension named `cloakroom` in
   `~/.gemini/extensions/cloakroom/` (`gemini-extension.json` and `hooks/hooks.json`, four hooks that call the
   `cloakroom` program). Your `settings.json` and your own hooks and extensions aren't touched. Gemini runs the
   hooks from its next start, in every folder; `gemini extensions list` shows it, `cloakroom doctor` too, and warns
   when the extension is disabled or Gemini's hooks are switched off.

What works as in Claude Code: the output of every tool (shell, file reads, MCP) is masked before the model sees it,
pictures are read by local OCR or held back, placeholders in shell commands, `write_file` and `replace` get their
real values back on your machine, real values are kept out of MCP calls and web tools, and a prompt holding a
secret is stopped with what to do next. What's different:

- Gemini can't swap a tool's output in place, so the masked output reaches the model as a "blocked" tool result
  that holds the masked text.
- Prompts can only be stopped, not masked. Gemini could rewrite a prompt on its way to the model, but it keeps the
  original in the conversation and sends that to Google on other calls no hook sees.
- Gemini lets a call through when a hook runs past its time limit. Cloakroom never waits for its model to load:
  until it's ready, checks run on the rules only (keys, cards, IDs).

To remove it: `cloakroom uninstall gemini` (deletes that folder, nothing else). More in
[Known limits](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-LIMITS.md#gemini-cli).

## In an editor

- **Claude Code in VS Code or JetBrains:** Cloakroom works as in the terminal. The VS Code extension runs Claude
  Code with your plugins, and the JetBrains plugin runs `claude` in the IDE's terminal.
- **Codex in VS Code** (OpenAI's Codex extension): the extension's own Codex reads the same `~/.codex/hooks.json`,
  so it loads Cloakroom's hooks. Trust them first in the `codex` CLI (`/hooks`, step 3 above); the Codex limits
  above apply.
- **Gemini Code Assist in VS Code:** Cloakroom does not protect you there. Its agent mode doesn't run the hooks
  of Gemini extensions. Use the Gemini CLI in a terminal instead (VS Code's own terminal is fine).
- **Antigravity**, and editors not listed here: not supported. Cloakroom installs nothing there.

More in [Known limits](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-LIMITS.md#editors).

## Ways to run

- **Full mode** (default): rules plus the local model. Catches keys and personal data written as plain text.
- **Rules mode** (`CLOAKROOM_MODE=rules`): no model, instant, installs with plain `uv tool install cloakroom`.
  Catches keys, card numbers and IDs, but misses most names and addresses.
- **Outside Claude Code:** `cloakroom check FILE` shows what would be masked, `cloakroom explain FILE` shows why,
  `cloakroom mask FILE` prints what Claude would see.
- **Dry run** (`CLOAKROOM_DRY_RUN=1`): lets everything through and logs what it would have done.
- **Settings** in `~/.cloakroom/config.json`: `"allow"` for your own exceptions, `"mask_all_dates": true` to mask
  every date (by default only birth dates and dates in someone's record), `"mask_own_username": true` to mask your
  own login name in paths too, `"allow_images": true` to let pictures through unchecked (with a warning),
  `"allow_secret_paths": ["tests/fixtures/*"]` (globs, relative to the project folder) for files where sample keys
  may be written (or committed) even though git would commit them, `"log_months"` for how many months of the
  activity log to keep (default 12).
- **Report and log check:** `cloakroom report`, see [Extra protections](#extra-protections).

Exceptions, backends, CI and troubleshooting: [the handbook](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-HANDBOOK.md).

## FAQ

**Does it send my data anywhere?** No. It downloads the detection model once from Hugging Face (a pinned
version); the OCR models come inside the package. Everything else runs on your own machine (127.0.0.1).

**How much slower is Claude?** Each tool call is checked by a small local service: about 20 to 70 ms on Apple
Silicon, where the model runs on the built-in GPU (an M5 Max to an 8 GB M1 Air), 60 ms on an Apple M-series CPU,
about 300 ms on a 2-core cloud VM. Without an Apple Silicon or NVIDIA GPU, a fast CPU version of the model is built
on first start. The first run downloads the model once (about 1 GB). `cloakroom doctor` prints your numbers.

**Will it mask things it shouldn't?** Sometimes, see above. Documented examples (Stripe test cards, `example.com`,
fictional 555 numbers), plain dates and job titles are left alone. Mark a false alarm on the dashboard and it
suggests a rule, or add exceptions to `allow` in `~/.cloakroom/config.json`. `cloakroom check FILE` previews masks.

**What doesn't it protect?** It stops accidental exposure; it isn't a sandbox. Out of scope: a prompt injection
(text in a file that steers Claude) set on smuggling data out piece by piece, what you type into a prompt (a prompt
can be blocked but not rewritten, and names alone go through), and Claude Code's own local logs. Keys are kept
out of files git would commit when Claude writes them with Write or Edit, but a file a shell command writes some other
way (a script, `cp`, `sed -i`) is not checked yet, so a commit made from it can still carry a key. Details:
[docs-LIMITS.md](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-LIMITS.md).

**What about PDFs, scans and photos?** Cloakroom pulls the text out on your machine and masks it like any file;
Claude gets the text, never the PDF or the picture. Scanned pages and photos (png, jpg) are read by OCR (reading
text from an image), running on your CPU with its models inside the package: no download, no cloud, no account.
Screenshots and images returned by MCP tools (a browser or desktop tool, say) get the same treatment: the picture
is read by local OCR and Claude gets the masked text in its place. The trade-off: Claude reads the text, it doesn't
see the picture (layout, colours, charts and anything that isn't text are lost), and OCR can miss or misread small
or faint text. Without OCR (no `cloakroom[ocr]`, or an Intel Mac) such an image is held back, and Claude is told why.
A scan it can't read in time, encrypted PDFs, figures in a notebook and other pictures are held back, and Claude is
told so; to show Claude a picture anyway, attach it to your own prompt. Page and time limits:
[known limits](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-LIMITS.md).

**How do I remove it?** See [Uninstall](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-SETUP.md#uninstall).

## More

- [Write-up](https://shanjeevrajendran.github.io/cloakroom/): why I built it and how masking, OCR, restore and the dashboard fit together
- [How it works](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-HOW-IT-WORKS.md): the detectors, the known-safe filter, the vault and the restore
- [Changelog](https://github.com/shanjeevrajendran/cloakroom/blob/main/CHANGELOG.md) · [Setup details](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-SETUP.md) · [Known limits](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-LIMITS.md) · [Benchmark](https://github.com/shanjeevrajendran/cloakroom/blob/main/bench/RESULTS.md)
- Found a way around it? Please report it privately: [SECURITY.md](https://github.com/shanjeevrajendran/cloakroom/blob/main/SECURITY.md)

## Author

Made by Shanjeev Rajendran · [GitHub](https://github.com/shanjeevrajendran) · [LinkedIn](https://www.linkedin.com/in/shanjeevrajendran)

## Credits

Detection model: [PII-Tracer](https://huggingface.co/perplexity-ai/PII-Tracer). The one-string hook command that
runs in both bash and PowerShell comes from [maisecrets](https://github.com/Mcpgate-de/maisecrets) (Apache-2.0).
Licensed Apache-2.0.
