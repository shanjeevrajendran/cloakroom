# Cloakroom handbook

How to install, run and tune Cloakroom. For what it is and how well it works, see the [README](README.md).

## 1. Pick how you'll run it

Easiest: add the plugin (below) and let it install Cloakroom on the next start. It uses uv or pipx if either is
installed, otherwise a private Python environment in `~/.cloakroom/venv` (needs Python 3.10+). Rules-only
protection is in place within seconds, then the model is fetched in the background and, on a machine without an
Apple Silicon or NVIDIA GPU, the fast CPU model is built too (progress in `~/.cloakroom/bootstrap.log`). If the
install fails, Claude Code says so and it retries after 10 minutes. The table is for installing by hand.

| Your machine | Install | What you get |
|---|---|---|
| Apple Silicon Mac, or an NVIDIA GPU | `uv tool install "cloakroom[model,torch]"` | Full mode on the GPU, about 20 ms per check |
| Any other machine (Intel Mac, most Windows and Linux) | `uv tool install "cloakroom[model,torch]"`, then once: `cloakroom export-onnx` | Full mode on the CPU. Before the export it works but is slow (about 0.5 s per check); after it, about 60 ms on a modern CPU |
| Just trying it, or a CI box | `uv tool install cloakroom` and `CLOAKROOM_MODE=rules` | Rules only: instant, no model download, but misses names and free-text personal data |

`pipx install ...` works the same way as `uv tool install ...`. The model extras need about 1 GB of disk on a Mac and up to
about 4 GB elsewhere (mostly PyTorch). Python 3.10 or newer.

To try it on one project first, install the plugin for that folder only: run
`claude plugin marketplace add shanjeevrajendran/cloakroom --scope local` and
`claude plugin install cloakroom@cloakroom --scope local` from inside the folder. Other projects stay untouched.

Or add the plugin for all your projects, inside Claude Code:

```
/plugin marketplace add shanjeevrajendran/cloakroom
/plugin install cloakroom@cloakroom
```

and check the machine:

```bash
cloakroom doctor
```

Every line should say `ok`, except `service: down`, which is normal before the first use. If `model` says FAIL,
the message tells you which extra is missing; until it's fixed, full mode blocks instead of letting data through.

## 2. The two modes

**Full** (the default) runs pattern rules and a local detection model (PII-Tracer). It catches keys, passwords,
card numbers, government IDs, and also names, addresses and other personal details written as plain text.

**Rules** runs the pattern rules only. It still catches keys with a known shape (AWS, GitHub, Stripe, Slack,
OpenAI, Google, JWTs, private keys), checksummed numbers (cards, IBANs, SSNs) and emails or names that sit next
to obvious context, but it misses most free-text personal data. Use it when you can't install the model, or when
you only care about secrets.

Set the mode for everything in `~/.cloakroom/config.json`:

```json
{ "mode": "rules" }
```

or for one shell with `CLOAKROOM_MODE=rules`, or for one command with `--mode rules`.

## 3. Backends (full mode only)

`CLOAKROOM_BACKEND` picks how the model runs. The default, `auto`, uses PyTorch when there's a GPU (Apple
Silicon or CUDA), otherwise the ONNX export if you've made one, otherwise PyTorch on the CPU.

- `cloakroom export-onnx` writes the CPU model under `~/.cloakroom/onnx/`. It needs PyTorch once and takes a few
  minutes (about 4 on a 2-core cloud machine).
- `CLOAKROOM_ONNX=int8` uses a smaller quantized export: a little faster, a few more false alarms.
- `CLOAKROOM_BACKEND=torch` or `onnx` forces one.

`cloakroom doctor` prints the backend in use and how long one check takes.

## 4. Day to day in Claude Code

Nothing changes in how you work. What you'll notice:

- **Placeholders.** Values in what Claude reads show up to Claude as `PII_PERSON_1`, `PII_EMAIL_1`, `PII_SECRET_1`
  and so on. The same value gets the same placeholder for the whole session.
- **Real values come back locally.** When Claude writes a file or runs a command with a placeholder, the real
  value is put back on your machine before it runs.
- **Short notices.** A line like `cloakroom: masked 2 PERSON, 1 EMAIL in Read output` tells you something was
  swapped. The first tool call of a session can wait a few seconds while the model loads.
- **Blocked prompts.** If a prompt holds a key, an email, a phone, a card or an ID number, it is blocked (prompts
  can't be rewritten) with a message saying what it found. Names alone and plain dates go through. To get past a
  block: put the data in a file and point Claude at it (best: it gets masked), or start the prompt with `cr!` to
  send it as it is, or type `cr: pause 10m` to pause prompt checks (`cr: resume` ends it). Files and command
  output stay masked throughout, and Claude can't use these commands or change Cloakroom's settings.
- **Edit.** Claude Code checks an edit's "find" text before Cloakroom sees it, so an edit whose find text holds a
  placeholder fails once. Claude is told to anchor on nearby text or rewrite the file, and usually recovers by
  itself.
- **Encoded dumps.** Output from `base64`, `xxd`, `od` and similar is decoded and checked; if it hides a value,
  the whole output is withheld.

- **Dashboard.** `cloakroom dashboard` opens your alerts in the browser (127.0.0.1 only). Label them correct,
  false alarm or missed; see section 6.

To turn Cloakroom off completely, disable the plugin from `/plugin` in Claude Code. To remove it, see
[Uninstall](docs-SETUP.md#uninstall).

## 5. Using it outside Claude Code

The same engine works on any file or pipe, which is handy for checking what it would do:

```bash
cloakroom check config.py        # what would be masked, by kind; never prints the values
cloakroom explain config.py      # every finding with the rule that found it, and what was skipped as safe
cloakroom mask config.py         # the masked text, as Claude would see it
cat app.log | cloakroom check -  # read from stdin
cloakroom check --mode rules .env
```

Other commands: `cloakroom status` (mode, service, vault sessions), `cloakroom serve` (start the local service
by hand; it normally starts on first use), `cloakroom doctor`, `cloakroom export-onnx`.

## 6. Fewer false alarms: your own exceptions

Cloakroom already leaves documented examples alone (published test cards and keys, `example.com`, fictional 555
numbers, loopback addresses, UUIDs and hashes). To add your own, list them under `allow` in
`~/.cloakroom/config.json`, either as exact values or as `re:` regular expressions matched against the whole value:

```json
{
  "mode": "full",
  "allow": ["Ada Lovelace", "support@mycompany.com", "re:ACME-[0-9]{6}"]
}
```

`cloakroom explain FILE` shows "on your allowlist" next to anything an entry let through.

**From the dashboard.** Mark an alert as a false alarm and Cloakroom suggests a "never mask" rule for the values you
tick; type what it missed and it suggests an "always mask" rule (a value with digits covers its shape). In
*Suggest* mode nothing changes until you click Approve. After 10 decisions (labels, approvals, dismissals) you can
switch to *Learn quietly*: each new rule is first replayed against your past labels and a built-in set of sample
secrets, and applies by itself only if it unmasks nothing you wanted hidden and masks nothing you called safe.
Rules for keys, passwords, cards and ID numbers always wait for your Approve, and every rule has Undo. These rules
are stored encrypted in `~/.cloakroom/rules.bin`; the `allow` list above stays yours to edit by hand.

## 7. Headless machines, CI and testing

- **Vault key.** The vault key lives in the OS secure store (Keychain, Credential Manager, Secret Service). On a
  machine without one, set `CLOAKROOM_VAULT_KEY` (base64), or Cloakroom falls back to a key file readable only by
  you, and `doctor` says so.
- **Dry run.** `CLOAKROOM_DRY_RUN=1` lets everything through and logs what Cloakroom would have done to
  `~/.cloakroom/dry-run.log`. Useful for trying it on a real project before trusting it.
- **Separate homes.** `CLOAKROOM_HOME` moves the config, vault and model files; `CLOAKROOM_PORT` moves the local
  service (default 37787).

## 8. Settings reference

| Setting | Where | Default | Values |
|---|---|---|---|
| mode | `config.json` `mode`, `CLOAKROOM_MODE`, `--mode` | `full` | `full`, `rules` |
| allow | `config.json` `allow` | none | exact values, `re:<regex>` |
| mask_all_dates | `config.json` | `false` | `true` masks every date, not only birth dates and dates in someone's record |
| mask_own_username | `config.json` | `false` | `true` masks your own login name in home-folder paths too |
| allow_images | `config.json` | `false` | `true` lets pictures from Read through unchecked, with a warning (by default they are held back) |
| port | `config.json` `port`, `CLOAKROOM_PORT` | `37787` | any free local port |
| backend | `CLOAKROOM_BACKEND` | `auto` | `auto`, `torch`, `onnx` |
| ONNX variant | `CLOAKROOM_ONNX` | `fp32` | `fp32`, `int8` |
| ONNX folder | `CLOAKROOM_ONNX_DIR` | `~/.cloakroom/onnx/<model version>` | a path |
| home | `CLOAKROOM_HOME` | `~/.cloakroom` | a path |
| vault key | `CLOAKROOM_VAULT_KEY` | OS secure store | base64 key |
| dry run | `CLOAKROOM_DRY_RUN` | off | any value turns it on |

## 9. When something's wrong

- **"NOT protecting this session" and every tool call is denied.** The plugin is on but the `cloakroom` program
  isn't installed, or isn't on the PATH Claude Code sees. Install it (`uv tool install "cloakroom[model,torch]"`)
  and restart Claude Code; on Windows also run `uv tool update-shell` (or `pipx ensurepath`) and open a new
  terminal. Or turn the plugin off with `/plugin disable cloakroom@cloakroom`. Your prompts still go through
  meanwhile, so you can ask Claude for help.
- **"port ... is used by another program, not Cloakroom".** Another local tool owns Cloakroom's port (for
  example a proxy). Pick a free one in `~/.cloakroom/config.json`, e.g. `{"port": 37788}`, and restart Claude Code.
  `cloakroom doctor` shows the port in use.
- **`model: FAIL`.** Install the extra it names, or switch to rules mode.
- **Windows, PyTorch won't load.** Install the Microsoft Visual C++ Redistributable
  (https://aka.ms/vs/17/release/vc_redist.x64.exe).
- **Too much gets masked.** Mark it a false alarm on the dashboard, or run `cloakroom explain` on the file to see
  which rule fired and add an `allow` entry.
- **A prompt keeps getting blocked.** Start it with `cr!`, or type `cr: pause 10m`. See section 4.
- **Dashboard says "signed out".** The service restarted; run `cloakroom dashboard` again.
- **Something got through.** Please open an issue with the kind of value (not the value itself) and where it
  appeared.

More: [How it works](docs-HOW-IT-WORKS.md) · [Setup details](docs-SETUP.md) · [Known limits](docs-LIMITS.md)
