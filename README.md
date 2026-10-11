<div align="center">

<img src="https://raw.githubusercontent.com/shanjeevrajendran/cloakroom/main/assets/hero.svg" alt="Cloakroom, a privacy layer for Claude Code. v0.1 benchmark on five fresh test sets: 0 of 1,005 personal data values leaked, 4 of 334 secrets and IDs leaked, 60 of 200 safe samples masked by mistake." width="880">

<p>
  <a href="#get-started">Get started</a> ·
  <a href="#results">Results</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#faq">FAQ</a> ·
  <a href="https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-LIMITS.md">Known limits</a>
</p>

</div>

Names, emails, addresses and API keys in the files Claude reads are swapped
for placeholders before the model sees them. When Claude writes a file, the real values go back in, on your machine.
Runs on macOS, Windows and Linux.

**Status:** v0.1. Tested on macOS and a real Windows machine (Linux in CI). It works; expect rough edges.

<div align="center">
  <img src="https://raw.githubusercontent.com/shanjeevrajendran/cloakroom/main/assets/demo.gif" alt="Claude Code reads a support ticket and .env through Cloakroom: the model sees PII_PERSON_1 and PII_SECRET_1, and reply.md on disk gets the real customer name back" width="820">
  <br><sub>Claude sees <code>PII_PERSON_1</code>. The reply it writes lands on your disk as "Hi Nadia Petrov".</sub>
</div>

## Why

Claude Code reads whatever your task touches: `.env` files, logs, customer exports, support tickets. All of it
goes to the model. Secret scanners catch keys with a known shape, but a customer's name in a ticket or an address
in a log looks like ordinary text to a regex.

## What's different

- **Catches personal data, not just keys.** A local detection model plus rules. Across five fresh test sets it
  leaked none of 1,005 planted names, emails, phone numbers, addresses and birth dates. The other tools we tested
  let 119 to 992 of them through.
- **Claude keeps working.** Placeholders stay the same for the whole session, and the real values are put back
  when Claude writes a file or runs a command locally.
- **Runs on your machine.** Detection, the encrypted vault and the restore are all local. The only network call is
  the one-time model download.
- **Cross-platform.** One install on all three, with the vault key in each system's own secure store:
  - **macOS:** Keychain. On Apple Silicon the model runs on the built-in GPU, about 20 ms per check.
  - **Windows:** Credential Manager, hooks that run in PowerShell, tested on a real Windows machine.
  - **Linux:** Secret Service, covered by CI on every change.
- **No GPU needed.** On an Apple M-series CPU a check takes about 60 ms.
- **Catches encoded dumps.** `base64`, `xxd` and `od` output is decoded and checked too.
- **Fails closed.** If Cloakroom isn't running, tool calls are denied instead of slipping through, and every prompt
  carries a warning with the fix, so you're never locked out of Claude Code.

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

## Get started

```bash
# 1. Install (about 4 GB with the model; pipx works too)
uv tool install "cloakroom[model,torch]"

# 2. No GPU? Make the fast CPU model once (a few minutes)
cloakroom export-onnx

# 3. Check this machine: Python, the key store, the model and the service
cloakroom doctor
```

```
# 4. Add the plugin, inside Claude Code
/plugin marketplace add shanjeevrajendran/cloakroom
/plugin install cloakroom@cloakroom
```

Python 3.10+. Skip step 2 on an Apple Silicon Mac or an NVIDIA GPU.

## Ways to run

- **Full mode** (default): rules plus the local model. Catches keys and personal data written as plain text.
- **Rules mode** (`CLOAKROOM_MODE=rules`): no model, instant, installs with plain `uv tool install cloakroom`.
  Catches keys, card numbers and IDs, but misses most names and addresses.
- **Outside Claude Code:** `cloakroom check FILE` shows what would be masked, `cloakroom explain FILE` shows why,
  `cloakroom mask FILE` prints what Claude would see.
- **Dry run** (`CLOAKROOM_DRY_RUN=1`): lets everything through and logs what it would have done.

Exceptions, backends, CI and troubleshooting: [the handbook](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-HANDBOOK.md).

## Dashboard (preview, coming in v0.2)

<div align="center">
  <img src="https://raw.githubusercontent.com/shanjeevrajendran/cloakroom/main/assets/dashboard-preview.png" alt="Preview of the Cloakroom dashboard: health status, values kept from the model, an alert opened to show what Claude saw next to the file on disk, and the learning panel" width="820">
  <br><sub>Design preview with example data. Not in the current release yet.</sub>
</div>

A local page served by Cloakroom itself, on your own machine only:

- **Alerts** colour-coded by severity. Open one to see what Claude saw next to your file, with real values blurred
  until you click reveal.
- **Your labels:** mark an alert *correct*, a *false alarm*, or point at something it *missed*.
- **Learning from your labels:** each label becomes a rule ("never mask Grace Hopper", "always mask ACCT-123456").
  In *Suggest* you approve every rule; once you've made enough decisions, *Learn quietly* applies them itself,
  but only after replaying them against your past decisions and a built-in safety set. Rules that would unmask a
  key, password, card or ID number always wait for you. The detection model itself never changes.
- **Health at a glance:** mode, backend, port and the last error, with the fix spelled out.

## Results

Cloakroom and four other Claude Code redaction tools, default settings, the same synthetic agent traffic,
offline. Five fresh test sets, written by a different model and each run once before any tuning on it: 1,575
planted values to catch and 200 safe samples that should be left alone.

| Tool | Personal data (1,005) | Keys, passwords, cards, IDs (334) | False alarms (200 safe samples) |
|---|---|---|---|
| **Cloakroom** | **0** | **4** | 60 |
| sensitive-canary (blocks the call instead of masking) | 119 | 40 | 16 |
| maisecrets | 557 | 97 | 21 |
| claude-code-redact, PII on | 607 | 101 | 31 |
| redact-hook | 817-822 | 143-157 | 35-40 |
| claude-code-redact, secrets only | 992 | 179 | 2 |

Some of these tools only aim at secrets, so their personal-data column shows scope as much as quality.
False alarms are Cloakroom's weak spot: it masked 60 of 200 safe samples, such as historical names, landmark
addresses and documented test keys. A false alarm costs you a placeholder where Claude needed the real text; a
miss sends the value to the model. Method and every run:
[bench/RESULTS.md](https://github.com/shanjeevrajendran/cloakroom/blob/main/bench/RESULTS.md). The maintainers of the compared tools were contacted before publishing.

## FAQ

**Does it send my data anywhere?** No. It downloads the detection model once from Hugging Face (a pinned
version). Everything else runs on 127.0.0.1.

**How much slower is Claude?** Each tool call is checked by a small local service: about 20 ms on an Apple GPU,
60 ms on an Apple M-series CPU, 400 ms on a 2-core cloud VM. `cloakroom doctor` prints yours.

**Will it mask things it shouldn't?** Sometimes, see above. Documented examples (Stripe test cards,
`example.com`, fictional 555 numbers) are left alone, and you can add your own exceptions to `allow` in
`~/.cloakroom/config.json`. `cloakroom check FILE` shows what would be masked.

**What doesn't it protect?** It stops accidental exposure; it isn't a sandbox. Out of scope: a prompt injection
set on smuggling data out piece by piece, text you paste into a prompt on purpose (that gets blocked, not
rewritten), and Claude Code's own local logs. Details: [docs-LIMITS.md](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-LIMITS.md).

**How do I remove it?** See [Uninstall](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-SETUP.md#uninstall).

## More

- [How it works](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-HOW-IT-WORKS.md): the detectors, the known-safe filter, the vault and the restore
- [Setup details](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-SETUP.md) · [Known limits](https://github.com/shanjeevrajendran/cloakroom/blob/main/docs-LIMITS.md) · [Benchmark](https://github.com/shanjeevrajendran/cloakroom/blob/main/bench/RESULTS.md)
- Found a way around it? Please report it privately: [SECURITY.md](https://github.com/shanjeevrajendran/cloakroom/blob/main/SECURITY.md)

## Credits

Detection model: [PII-Tracer](https://huggingface.co/perplexity-ai/PII-Tracer). The one-string hook command that
runs in both bash and PowerShell comes from [maisecrets](https://github.com/Mcpgate-de/maisecrets) (Apache-2.0).
Licensed Apache-2.0.
