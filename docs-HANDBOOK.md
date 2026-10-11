# Cloakroom handbook

How to install, run and tune Cloakroom. For what it is and how well it works, see the [README](README.md).

## 1. Pick how you'll run it

| Your machine | Install | What you get |
|---|---|---|
| Apple Silicon Mac, or an NVIDIA GPU | `uv tool install "cloakroom[model,torch]"` | Full mode on the GPU, about 20 ms per check |
| Any other machine (Intel Mac, most Windows and Linux) | `uv tool install "cloakroom[model,torch]"`, then once: `cloakroom export-onnx` | Full mode on the CPU. Before the export it works but is slow (about 0.5 s per check); after it, about 60 ms on a modern CPU |
| Just trying it, or a CI box | `uv tool install cloakroom` and `CLOAKROOM_MODE=rules` | Rules only: instant, no model download, but misses names and free-text personal data |

`pipx install ...` works the same way as `uv tool install ...`. The model extras need about 4 GB of disk (mostly
PyTorch). Python 3.10 or newer.

Then add the plugin inside Claude Code:

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
- **Blocked prompts.** If you paste a key or personal details straight into a prompt, it is blocked (prompts
  can't be rewritten) with a message saying what it found. Put the data in a file and point Claude at it instead.
- **Edit.** Claude Code checks an edit's "find" text before Cloakroom sees it, so an edit whose find text holds a
  placeholder fails once. Claude is told to anchor on nearby text or rewrite the file, and usually recovers by
  itself.
- **Encoded dumps.** Output from `base64`, `xxd`, `od` and similar is decoded and checked; if it hides a value,
  the whole output is withheld.

To pause Cloakroom, disable the plugin from `/plugin` in Claude Code. To remove it, see
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

## 7. Headless machines, CI and testing

- **Vault key.** The vault key lives in the OS secure store (Keychain, Credential Manager, Secret Service). On a
  machine without one, set `CLOAKROOM_VAULT_KEY` (base64), or Cloakroom falls back to a key file readable only by
  you, and `doctor` says so.
- **Dry run.** `CLOAKROOM_DRY_RUN=1` lets everything through and logs what Cloakroom would have done to
  `~/.cloakroom/dry-run.log`. Useful for trying it on a real project before trusting it.
- **Separate homes.** `CLOAKROOM_HOME` moves the config, vault and model files; `CLOAKROOM_PORT` moves the local
  service (default 8787).

## 8. Settings reference

| Setting | Where | Default | Values |
|---|---|---|---|
| mode | `config.json` `mode`, `CLOAKROOM_MODE`, `--mode` | `full` | `full`, `rules` |
| allow | `config.json` `allow` | none | exact values, `re:<regex>` |
| port | `config.json` `port`, `CLOAKROOM_PORT` | `8787` | any free local port |
| backend | `CLOAKROOM_BACKEND` | `auto` | `auto`, `torch`, `onnx` |
| ONNX variant | `CLOAKROOM_ONNX` | `fp32` | `fp32`, `int8` |
| ONNX folder | `CLOAKROOM_ONNX_DIR` | `~/.cloakroom/onnx/<model version>` | a path |
| home | `CLOAKROOM_HOME` | `~/.cloakroom` | a path |
| vault key | `CLOAKROOM_VAULT_KEY` | OS secure store | base64 key |
| dry run | `CLOAKROOM_DRY_RUN` | off | any value turns it on |

## 9. When something's wrong

- **Every tool call is denied.** `cloakroom` isn't on the PATH Claude Code sees. Run `cloakroom doctor`; on
  Windows run `uv tool update-shell` (or `pipx ensurepath`) and open a new terminal.
- **`model: FAIL`.** Install the extra it names, or switch to rules mode.
- **Windows, PyTorch won't load.** Install the Microsoft Visual C++ Redistributable
  (https://aka.ms/vs/17/release/vc_redist.x64.exe).
- **Too much gets masked.** Run `cloakroom explain` on the file to see which rule fired, then add an `allow` entry.
- **Something got through.** Please open an issue with the kind of value (not the value itself) and where it
  appeared.

More: [How it works](docs-HOW-IT-WORKS.md) · [Setup details](docs-SETUP.md) · [Known limits](docs-LIMITS.md)
