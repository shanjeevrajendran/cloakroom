# Changelog

## 0.4.2 (2026-10-15)
- Windows hooks start `powershell` with the same line as 0.2.3, the form the Claude plugin directory accepts
  (0.4.0 and 0.4.1 were held back by its launcher check). No behaviour change; Python code is the same as 0.4.1.

## 0.4.1 (2026-10-15)
- Masking: when one model span swallows several rule findings, the rule labels are kept, so
  `Priya Raman 415-820-4471 SSN 529-31-8846` masks as person, phone, "SSN", SSN instead of one long span. A model
  finding on the same span as a rule finding is judged by the rule's kind, so fictional 555-01xx numbers stay unmasked.
  The "Loading weights" progress bar no longer prints.
- Locked Keychain (for example over SSH): no crash. User rules are skipped with one warning, Cloakroom never mints a
  new vault.key when the OS secure store refuses access, and `cloakroom doctor` shows the real error.
- Windows hooks start `pwsh` or `powershell` by name (whichever runs the hook) instead of computing the program path.

## 0.4.0 (2026-10-14)
- Scanned PDFs and photos are now read and masked instead of held back: with the new optional `ocr` extra
  (`cloakroom[ocr]`: RapidOCR on ONNX Runtime, pdfium to render pages; models inside the wheel, no system programs,
  no network), pages without a text layer and png/jpg pictures read with Read are OCR'd locally and the text is
  masked like any file. Mixed PDFs keep their text layer for typed pages. In OCR text, any run of 10+ digits is masked
  as an account number, since OCR drops or adds spaces inside numbers. Up to 20 pages and 30 seconds per Read
  (`ocr_max_pages`, `ocr_timeout`; `"ocr": false` turns it off); the hook log notes `ocr pages=N ms=M`.
- Held back as before: encrypted PDFs, unsupported pictures, notebook figures, and any OCR failure or timeout.
  Without the extra, behaviour is exactly 0.3.0.
- The plugin's first-start install adds the `ocr` extra to stage 2 (about 100 MB more); if it fails to install,
  stage 2 is retried without it, so OCR never blocks the model.

## 0.3.0 (2026-10-14)
- Fewer false alarms on code and config: Cloakroom now leaves alone values that say they were scrubbed
  (`login_and_password_removed`, `REDACTED_TOKEN`), template values (`$t(label)`, `{{ var }}`, `${var}`), and
  variable names under a secret key that end in the secret word (`token: opts.AccessToken`,
  `secret = "NATS_SECRET"`). Literal values, quoted values and anything with digits are still masked, and the
  benchmark secrets caught before are still caught. Details: `bench/precision-2026-10/levers.md`.

## 0.2.4 (2026-10-14)
- PDFs that Claude reads with its Read tool now arrive as masked text: Cloakroom extracts the text locally (pypdf,
  pure Python, now a core dependency), normalises it and masks it like any other file. Before, the PDF went to the
  model unchecked as a document. Pictures, scanned or encrypted or damaged PDFs, notebook figures and unknown Read
  types are held back with a note (scans: run `pdftotext file -`, whose output is masked); `"allow_images": true`
  lets pictures through with a warning. Not covered: pictures returned by MCP and other non-Read tools (see LIMITS).
- Full mode: a model span that started at a key name or on `=` (`STRIPE_KEY=sk_...`) is trimmed to the value, so the
  key name is no longer dropped on write-back. Credential pairs, URLs and base64 padding stay whole.
- A crash while checking a non-text read now holds the read back instead of letting it through.

## 0.2.3 (2026-10-12)
- The plugin's hooks now run fixed scripts inside the plugin (`scripts/hook.sh`, `scripts/hook.ps1`) instead of long
  inline commands, so the Claude plugin directory can read what each hook runs. No behaviour change: the same
  lookup, the same messages when Cloakroom isn't installed yet, the same first-start install and 10-minute retry.
  The hooks no longer ask uv for its tool folder; they look in `~/.local/bin`, where uv and pipx put the program
  by default (a custom `UV_TOOL_BIN_DIR` needs `cloakroom` on your PATH).
- `cloakroom --version` prints the installed version.

## 0.2.2 (2026-10-12)
- Linux and other CPU-only machines with 8 GB: the first-start CPU model build no longer runs out of memory (it skips
  the opt-in int8 copy), and a build that is cut short can no longer leave a half model behind. In 0.2.1 such a
  machine kept withholding tool output because the half-built model failed to load. Cloakroom now uses the CPU model
  only when its tokenizer is there too.
- Ubuntu/Debian without `python3-venv` (the default install): the first-start installer says so and how to fix it
  (`sudo apt install python3-venv`, or install uv) instead of a bare "install failed".

## 0.2.1 (2026-10-12)
- Windows with Git Bash: the hooks now find Cloakroom where the installer puts it (`venv\Scripts\cloakroom.exe`)
  and start the PowerShell installer. In 0.2.0 they reported "not installed" even after the install, so every tool
  call stayed blocked for Windows users who have Git (Claude Code runs hooks in Git Bash when it is there).
- The first-start install note also appears on your first prompt of the session: Claude Code can show only one
  plugin's start-up message, so ours could be hidden behind another plugin's.

## 0.2.0 (2026-10-12)

**Install**
- The plugin installs Cloakroom itself on first start: rules-only protection within seconds, the model in the
  background, then full protection switches on by itself. It uses uv, else pipx, else a private virtualenv in
  `~/.cloakroom` (Python 3.10+), and builds the fast CPU model on machines without an Apple Silicon or NVIDIA GPU.
  A failed install is reported and retried after 10 minutes. `cloakroom export-onnx --if-no-gpu` skips on a GPU.
  The service starts as soon as the install finishes, so the first prompt doesn't wait for the model to load.
- `cloakroom doctor` looks for the program where the plugin does (PATH, uv's tool folder, ~/.local/bin,
  ~/.cloakroom/venv) instead of warning when it isn't on PATH.

**Dashboard and learning**
- `cloakroom dashboard`: a local page (127.0.0.1, one-time sign-in link) with every alert, the kinds found and the
  check time. Real values show only when you click Reveal.
- Label alerts correct, false alarm or missed. A false alarm suggests a "never mask" rule, a miss an "always mask"
  rule; you approve, dismiss or undo each one.
- Learn quietly (after 10 decisions): new rules apply by themselves only after a replay test on your past labels
  and a set of sample secrets. Rules for keys, passwords, cards and IDs always wait for you.
- Value-free activity log; labels and rules are encrypted like the vault.

**Fewer blocked prompts**
- A blocked prompt now says how to get past it: `cr!` at the start sends that prompt as it is, `cr: pause 10m`
  pauses prompt checks, `cr: resume` ends the pause. Tool output stays masked throughout, and Claude can't change
  Cloakroom's settings.
- Prompts holding only names go through (a name next to an email, phone, key or ID still blocks).
- Plain dates are no longer masked: only birth / death / age dates and dates in someone's record
  (`"mask_all_dates": true` brings back the old behaviour).
- Your own login name in a home-folder path is no longer masked (`"mask_own_username": true` to change it); other
  usernames are labelled USERNAME instead of ACCOUNT.
- File listings pass as they are: your login and its group in `ls -l`'s owner column, link counts and the
  lower-case word `claude` (the command, the `.claude` folder) are not names, and a listing whose dates repeat is
  no longer withheld as encoded output.

**Detection**
- Values under address keys, birth dates in ISO date-time and written forms, short passwords and PINs under their
  keys are caught.
- SWIFT / FIX message field tags are no longer read as account numbers; job titles such as "Support Agent" are no
  longer read as names.
- Model matches that ran across CSV rows are split back (rows no longer vanish from what Claude sees), and matches
  are widened to whole words.
- Benchmarked on public datasets for the first time: TAB court rulings recall 0.95 / precision 0.81, Gretel finance
  documents 0.85 / 0.52 (`bench/public/RESULTS.md`).

## 0.1.7 (not released on its own; included in 0.2.0)
- The plugin page's install hint installs a model (`cloakroom[model,torch]`; `[model]` alone has no model file).
- plugin.json gains author, homepage and repository.
- CSV rows no longer merge in masked output; dates are no longer cut mid-word.

## 0.1.0 - 0.1.6
First public releases: rules plus a local detection model, encrypted vault, restore for local tools, encoded-dump
checks, fail-closed hooks for bash and PowerShell, `cloakroom doctor`. See the git history for details.
