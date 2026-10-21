<div align="center">

<img src="https://raw.githubusercontent.com/shanjeevrajendran/cloakroom/main/assets/hero.svg" alt="Cloakroom, a privacy layer for Claude Code: names, emails, addresses and API keys are swapped for placeholders before the model sees them." width="880">

<p>
  <a href="https://www.youtube.com/shorts/U9lN4emgeE0">▶ 40-second demo</a> ·
  <a href="#get-started">Get started</a> ·
  <a href="#when-cloakroom-blocks-your-prompt">Blocked prompt?</a> ·
  <a href="#results">Results</a> ·
  <a href="#how-it-works">How it works</a> ·
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

**Hand Claude the files you'd never paste into a chat:**

- **Bank statements** (PDF, scan or CSV): "sort a year of spending by category." Claude sees amounts and merchants, not your name, address or account number.
- **Support tickets:** "draft replies to this week's tickets." Claude writes to a stand-in name (`PII_PERSON_1`);
  the drafts saved on your disk have the real names.
- **Medical or HR records:** names, birth dates, addresses and ID numbers are masked, so Claude can summarise or tidy a record without knowing whose it is.
- **`.env` files and logs:** Claude fixes the config without ever seeing the password or API key.
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

- **Secrets don't leave through a command:** a command that would send a known secret off your machine (curl,
  wget, ssh, scp, rsync, nc, git push with credentials, a literal token in an `Authorization` header) is held, and
  the message names what it found by placeholder only. If you meant it, type `cr: allow`, then ask Claude to run
  the same command again: it passes once.
- **Encoded copies count:** a secret Cloakroom has seen is also caught base64-encoded, URL-encoded or JSON-escaped.

Details: [docs-HOW-IT-WORKS.md](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-HOW-IT-WORKS.md).

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
settings, offline, tested 12 to 14 October 2026. maisecrets' maintainers reproduced our numbers and pointed out
where the older sets were unfair to them (keys in formats vendors don't issue, and the `regions` setting); the
key-format set and the setting below are the fix. The maintainers of the compared tools were contacted before
publishing. Method, versions and every run: [bench/RESULTS.md](https://github.com/shanjeevrajendran/cloakroom/blob/main/bench/RESULTS.md).

**Made-up agent traffic** (five sets written by a different model: chats, logs, config files, spreadsheets, support
tickets): 1,339 key and personal values (the rest are in RESULTS.md) and 200 safe look-alikes. Fewer is better.
These sets have been seen by now, so Cloakroom's second row is not an honest score; the first row is its run before
any tuning on them.

| Tool | Personal data leaked (of 1,005) | Keys, passwords, cards, IDs leaked (of 334) | False alarms (of 200 safe samples) |
|---|---|---|---|
| **Cloakroom, first run** | **0** | 4 | 60 |
| Cloakroom today (has seen these sets, so not a fair score) | 1 | **2** | 23 |
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
| **Cloakroom** | **0** | 3 |
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
| **Cloakroom** | **73** (65) | 22 (25) |
| maisecrets | 58 (**68**) | 7 (6) |
| claude-code-redact | 36 (31) | 8 (7) |
| sensitive-canary (blocks the call instead of masking) | 30 (27) | 21 (18) |
| redact-hook, default | 27 (19) | **5** (**4**) |
| redact-hook, aggressive | 36 (26) | 12 (20) |

Without the test-vector repository, Cloakroom catches the most; with it, maisecrets does, and maisecrets is the most
precise either way (of what it masks, 86 to 91 in 100 are real secrets, against Cloakroom's 71). Cloakroom masks far
more harmless look-alikes (3,903 against maisecrets' 997 on the whole set): false alarms are Cloakroom's main open
problem ([precision, recall and F1 for every tool](https://github.com/shanjeevrajendran/cloakroom/blob/main/bench/RESULTS.md)).

**On public datasets we didn't write** (run once; details in
[bench/public/RESULTS.md](https://github.com/shanjeevrajendran/cloakroom/blob/main/bench/public/RESULTS.md)):

| Dataset | Personal details found (of every 100) | Masks that were not labelled personal data (of every 100) |
|---|---|---|
| [TAB](https://github.com/NorskRegnesentral/text-anonymization-benchmark), real court rulings from the European Court of Human Rights | 95 | 19 |
| [Gretel](https://huggingface.co/datasets/gretelai/synthetic_pii_finance_multilingual), made-up finance documents | 85 | 48 |

On Gretel, over half of those extra masks are number strings Cloakroom treats as account or reference numbers; the
rest are mostly names and addresses Gretel leaves unlabelled.

## Ways to run

- **Full mode** (default): rules plus the local model. Catches keys and personal data written as plain text.
- **Rules mode** (`CLOAKROOM_MODE=rules`): no model, instant, installs with plain `uv tool install cloakroom`.
  Catches keys, card numbers and IDs, but misses most names and addresses.
- **Outside Claude Code:** `cloakroom check FILE` shows what would be masked, `cloakroom explain FILE` shows why,
  `cloakroom mask FILE` prints what Claude would see.
- **Dry run** (`CLOAKROOM_DRY_RUN=1`): lets everything through and logs what it would have done.
- **Settings** in `~/.cloakroom/config.json`: `"allow"` for your own exceptions, `"mask_all_dates": true` to mask
  every date (by default only birth dates and dates in someone's record), `"mask_own_username": true` to mask your
  own login name in paths too, `"allow_images": true` to let pictures through unchecked (with a warning).

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
can be blocked but not rewritten, and names alone go through), and Claude Code's own local logs. Details:
[docs-LIMITS.md](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-LIMITS.md).

**What about PDFs, scans and photos?** Cloakroom pulls the text out on your machine and masks it like any file;
Claude gets the text, never the PDF or the picture. Scanned pages and photos (png, jpg) are read by OCR (reading
text from an image), running on your CPU with its models inside the package: no download, no cloud, no account.
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
