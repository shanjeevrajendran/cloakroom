# Changelog

## Unreleased
- `cloakroom install git-hook` works on Windows: your commits and pushes are checked whether you commit from Git
  Bash, PowerShell or cmd, also when Cloakroom is installed in a folder with a space in its name. The git hook's
  tests now run on Windows too.

## 0.7.1 (2026-10-25)
- Skipped checks are visible: when a check lets a call through without running in full (a commit or push over 4 MB
  or slower than 2 s, a git error, git too slow to say whether a written key would be committed, a new file over
  2 MB in a commit, or the model not ready so only the rules checked), the event log gets a "skipped" line (reason,
  check, size and time; never values), you're told once per session per reason ("Cloakroom didn't check this git
  push: it was larger than 4 MB") in Claude Code, Codex and Gemini CLI, and `cloakroom report` has a "Checks
  skipped" section. The dashboard counts them on the "Tool calls checked" tile. A file over 512 KB that a command
  names is logged too, without a note: the command's output is still masked.
- `cloakroom install git-hook`: also checks commits you make yourself, with the same rules and your own known
  secrets. Adds `pre-commit` and `pre-push` hooks to the current repo (`core.hooksPath` respected), leaves another
  tool's hook alone, and is safe to run twice. Skip once with `--no-verify`; `cloakroom uninstall git-hook` removes it.
  A stopped commit or push is logged as held, and one it couldn't check (too large, too slow, git error) as skipped
  ("git commit (git hook)"), with the same line on the terminal.
- Codex: `"codex_hold_long_running": true` in `~/.cloakroom/config.json` holds commands that obviously keep running
  (`tail -f`, `watch`, dev servers, `docker logs -f`, `ping` without `-c`, `--watch`...), whose later output Codex
  never shows a hook, with one line saying why; `cr: allow` lets one run once. Off by default; holds are counted as
  events (`"hold": "long-running"`) so false holds can be measured.
- Gemini CLI: a call answered on the rules while the model starts is logged as a skipped model check and noted once
  per session (it was noted on every call). `cloakroom doctor` warns when Cloakroom's extension is disabled
  (`gemini extensions disable`) or Gemini's hooks are off (`hooksConfig.enabled: false`, or ours in
  `hooksConfig.disabled`), and says "can't tell" when it can't read those files.

## 0.7.0 (2026-10-24)
- Codex: Cloakroom also protects OpenAI's Codex CLI (Mac and Linux). `cloakroom install codex` adds its hooks to
  `~/.codex/hooks.json` (your own hooks stay), `cloakroom uninstall codex` takes them out, and `cloakroom doctor`
  says whether they are there. Shell output and MCP results are masked, placeholders in shell commands and patches
  get their real values back, real values are kept out of MCP calls, and a prompt holding a secret is stopped with
  what to do next. Trust the hooks once in Codex (`/hooks`). Open gap: output of a command still running when Codex
  stops waiting isn't masked ([limits](docs-LIMITS.md#codex)).
- Gemini CLI: Cloakroom also protects Google's Gemini CLI (Mac and Linux). `cloakroom install gemini` writes a
  Gemini extension of its own to `~/.gemini/extensions/cloakroom/` (nothing else is changed), `cloakroom uninstall
  gemini` deletes it, and `cloakroom doctor` says whether it is there. Every tool's output is masked (pictures read
  by OCR or held back, PDFs held back), placeholders in shell commands, `write_file` and `replace` get their real
  values back, real values are kept out of MCP and web tools, and a prompt holding a secret is stopped. The hooks
  never wait for the model to load: Gemini would let the call through on a timeout
  ([limits](docs-LIMITS.md#gemini-cli)).
- Windows: the event log is written with plain line ends, so its hash chain checks out (a log from 0.6.6 with
  Windows line ends still reads), and file names in it start with `~` for your home folder whichever slash the path
  uses, so `cloakroom report` keeps the folder class.
- Git Bash on Windows: the commit hold follows `cd /c/...` and `git -C /c/...` to the right repository.
- Fail-closed hooks: the plugin's launchers (`hook.sh` for bash and Git Bash, `hook.ps1` for PowerShell) now stop a
  tool call when the cloakroom program answers nothing or something that isn't JSON: output is withheld, the call
  is denied, a prompt still goes through with a warning (`cloakroom doctor` says what is wrong). The program answers
  `{}` on a clean pass; a program older than the plugin, still waiting for its upgrade, may answer nothing.

## 0.6.6 (2026-10-24)
- Disguised copies of known values: a secret Cloakroom has seen is also caught as an MD5, SHA-1 or SHA-256 hash, as
  hex (plain, `0x`, spaced, colon or `\x` escapes, also hex of `user:secret`), and reversed (keys and passwords of
  12+ characters). A known email is caught as its hash; a known card or account number's last 4 digits after
  "ending in", "last 4" or `****`; a known email's distinctive local part on its own. Each copy keeps its source's
  kind with its own placeholder, so restore returns the exact form, and copies never seed further copies.
- Fix: an encoded copy glued to a letter or digit (`0x...`, `%3D...`) is masked with the word around it, so restore
  finds its placeholder.

## 0.6.5 (2026-10-24)
- Bank numbers: US routing numbers (ABA checksum and a routing word or field) and bank account numbers (an account
  word or field, a CSV column of that name, a NACHA entry line, or a routing number in the same record) are masked
  as a new kind, `PII_BANK_n`. Bare digits are never matched. Known bank numbers are also looked for encoded, like
  cards and IBANs.
- Money-movement hold: a tool call that would move money (a write to a listed payment, payout or transfer API from
  a shell command or payment CLI, or an MCP tool named for it) is held with one line naming the endpoint or tool;
  `cr: allow` lets it run once and the log records both. On by default; `"money_hold": false` turns it off, and
  `"money_hosts"` / `"money_tools"` in `~/.cloakroom/config.json` extend the lists. A call that trips both this and
  the egress hold shows one hold and needs one `cr: allow`.
- Bank numbers leave the benchmarks where 0.6.2 put them (CredData, the 200 safe samples, Gretel, ai4privacy, TAB):
  counting placeholders like `Account Number: 123456789` are left alone, as for cards, and a bank word inside a git
  commit hash or a hex key is not read as a label.

## 0.6.4 (2026-10-24)
- 0.6.3 was tagged but never released (an install message set a failing exit status on Linux); its changes ship here.
- Keychain: a refused read of the vault key (you clicked Deny, or the Keychain is locked, as over SSH) is now
  remembered until `cloakroom serve` restarts or you run `cloakroom doctor --retry-key`, so macOS asks once instead
  of on every call. You see one message saying your own rules are off and how to turn them back on (built-in checks
  keep running). Installs and upgrades on a Mac now say in advance to click Always Allow.

## 0.6.2 (2026-10-24)
- Fewer false alarms: Cloakroom now leaves alone harmless text it used to mask in code and config. That covers package
  versions (`minipass-flush: ^1.0.5`), short numbers on code constants whose name has no secret word in it
  (`SYS_FUTEX_WAIT = 5323`), GPG key ids, translation and UI strings under message or label keys
  (`"incorrectPassword": "Wrong user name or password"`), variable names under a password key
  (`password: req.body.password`), AWS's documentation account and key ids, XAML resource names and ChangeLog file
  entries. On the CredData public-code set false alarms drop from 3,903 to 3,457 with the same 9,353 real secrets
  caught; holdout leaks, the 200 safe samples (20 false alarms) and the public datasets are unchanged.
  Levers and numbers: `bench/false-alarms-0.6.2/levers.md`.
- `cloakroom report` keeps the folder class when your home folder's name is masked.

## 0.6.1 (2026-10-23)
- Signed placeholders: each placeholder now ends in a short tag only your machine can make (`PII_EMAIL_1_k7q2ab`).
  Cloakroom restores a real value only for a placeholder it issued in this session. A guessed number, a wrong tag
  or a placeholder from another session stays as text, Claude is told so, and the activity log counts a refused
  restore (no values). After three in one session you see one line: "cloakroom: 3 placeholder guesses refused this
  session". This closes the route where a planted file names `PII_SECRET_1` without ever seeing it.
- Deliberate change on upgrade: placeholders from before this version (`PII_EMAIL_1`) are no longer restored. In a
  session you resume after upgrading, Claude is told the old placeholder wasn't restored and to ask you for the value
  again, and you see "cloakroom: left 1 placeholder from before 0.6.1 as text". The values that session already knew
  keep their numbers under signed placeholders. Files on disk are not touched.
- File log: when Cloakroom puts real values into a file Claude writes (Write, Edit, MultiEdit, NotebookEdit or a
  local shell command), it records where they sit: path, position, a hash of the value and of the file, kind and
  placeholder, encrypted like the vault and never the value itself. Later output from that file (Read, cat, grep,
  git diff, a test echoing it) is masked again before detection runs, in the same session (same placeholder) and in
  new ones (a new placeholder), even where detection alone would miss a name. An entry ends when the file is deleted
  or no longer holds the value there. The log keeps at most 5,000 positions or 10 MB, oldest files first, and
  is searched only for files under the session's working folder and the file being read.

## 0.6.0 (2026-10-23)
- Commit and push check: before `git commit` or `git push` runs from Claude, Cloakroom reads the staged change
  (with `-a`, paths or a `git add` in the same command, the working tree and new files too) or the commits no remote
  has yet. A key or password there (known values, your always-mask entries, encoded forms, the key rules; sample
  keys and `allow_secret_paths` excepted) holds the command like the egress hold: one line names the file and the
  placeholder or kind, and `cr: allow` lets it run once. Git errors, a change set over 4 MB or a check over two
  seconds let the command run as before.
- Interpreter one-liners that can open a connection are network commands: `python -c`, `node -e`/`-p`, `ruby -e`,
  `perl -e`, `php -r` and `deno eval` when the code mentions a socket, http, urllib, requests, fetch, net or a URL,
  and `bash -c`/`sh -c` around a network command. Real values are not restored into them and known secrets in them
  are held.
- Agent config files: a live token written into `.mcp.json`, `.claude/settings.json`, `.cursor/mcp.json` or
  `claude_desktop_config.json` inside a repo (and not git-ignored) is denied, with a note to use an environment
  reference such as `${GITHUB_TOKEN}`.
- Fewer false denies from 0.5.2: `->`, `=>` and `>=` in a shell command are no longer read as a redirect, and an edit
  that keeps a key the file already holds is allowed (only a newly added key is denied).
- Tamper-evident log: each line of the event log carries the SHA-256 of the line before it. New lines for
  `cr: pause` / `cr: resume`, `cr: allow`, `cr!`, fail-closed calls, dry runs and session start (version, mode,
  hooks). One file per month, kept `"log_months"` months (default 12), with a line when a month is deleted. Files
  from earlier versions are still read and never rewritten; the chain starts with the first new line.
- `cloakroom report [--since DATE] [--format csv|json] [--verify]`: counts per day and session by data kind and
  action, no values, no placeholder ids, paths reduced to extension and top folder, with a plain statement of what it
  covers and what it doesn't. `--verify` checks the chain and prints the head hash. The dashboard has an Export
  button for the same CSV.
- `cloakroom clean-history [--dry-run]`: finds keys and passwords in old Claude Code transcripts and the prompt
  history, counts them per project, and on your yes replaces them, keeping a backup you delete. The first run is
  always a dry run; files changed in the last 10 minutes (an open session) are skipped.

## 0.5.2 (2026-10-23)
- A key or password is no longer written into a file git would commit. When Write, Edit, MultiEdit, NotebookEdit or a
  local shell redirect (`>`, `>>`, `tee`) would put one into a file that is in a git repo and not git-ignored, the
  call is denied and Claude is told to put the value in `.env` and read it from the environment. This covers values
  Cloakroom would restore from a placeholder and keys Claude types itself. Files outside a repo, ignored files and
  `.env*`, `.envrc`, `*.pem`, `*.key`, `*.p12`, `credentials*` files are written as before; personal data (names,
  emails, phone numbers) is not checked. Fixtures and docs with sample keys: list them in `"allow_secret_paths"`.
  If git can't answer within a second, the write goes through as before.

## 0.5.1 (2026-10-21)
- Fix: a hex `CLOAKROOM_VAULT_KEY` (`openssl rand -hex 32`) is now accepted and a wrong-length key gets a clear error,
  instead of a vault write failing with ValueError. When an MCP result can't be checked, each image becomes a text
  block with the reason, in both `updatedToolOutput` and `updatedMCPToolOutput`.
- Updates itself when the plugin updates: an install made by the plugin, uv or pipx that is older than the plugin's
  release is upgraded in the background, with the same extras, and the running service restarts on the new version.
  The old version keeps protecting until the new one is in place. A newer, dev or editable install is never
  touched; an older one installed another way (Homebrew, system pip, conda) gets the upgrade steps once, at the next
  session start. `/health` now reports the version.

## 0.5.0 (2026-10-20)
- Egress hold: a shell command that would send a known secret off this machine (curl, wget, ssh, scp, rsync, nc,
  git push or remote with credentials, a literal token in an `Authorization` / `X-Api-Key` / `token=` header) is
  held. The reason names the placeholder or rule, never the value. Type `cr: allow`, then rerun the same command,
  to send it once.
- Secrets Cloakroom has seen are also caught base64-encoded (standard or URL-safe, any padding), URL-encoded or
  JSON-escaped, in prompts, tool inputs and tool output.
- MCP tools: screenshots and other images in an MCP tool's result are read by local OCR and passed on as masked
  text, like pictures read with Read; the pixels are not sent. Without OCR (no `ocr` extra, Intel Mac), or when OCR
  fails or finds no text, the image is held back with a one-line reason. `"allow_images": true` still lets
  pictures through with a warning. Claude gets the text, not the picture, and small text can be missed.
- Fewer false alarms: filler placeholders (a key or ID that is one repeated character, or cut short with `...`),
  standard and spec numbers right after RFC, ISO, IEC, IEEE, ICAO, ECMA or PEP, and the default dates 1970-01-01
  and 1900-01-01 are no longer masked. Holdout false alarms 26 to 22, leaks unchanged.
- A failed model install now shows numbered steps in Claude's message (what happened, how to retry, the log, Intel Macs), like the installer's other failure messages.

## 0.4.4 (2026-10-19)
- macOS and Linux: the plugin installs Cloakroom on a machine with nothing on it, like Windows. When neither uv nor
  pipx is there it fetches uv (with curl, else wget) before trying a virtualenv, and uv brings its own Python 3.12,
  so no system Python or `python3-venv` is needed.
- The uv fetch is pinned on every system: the uv 0.13.1 installer from astral.sh, run only when its SHA-256 matches
  the one in the plugin; on a mismatch it isn't run, the log says so, and the install falls back.
- Every install route puts `~/.local/bin` on the PATH of new terminals (`uv tool update-shell`, `pipx ensurepath`,
  or the same profile line for the virtualenv route), and logs which files it changed.
- When the install needs you, the plugin's message and `~/.cloakroom/bootstrap.log` give numbered steps for the
  cause (no curl or wget, offline, uv checksum mismatch, model download failed), each ending with restarting Claude
  Code. `bootstrap.sh --check` now also counts curl or wget as something to install with.
- Intel Mac: rules-only protection, said plainly (PyTorch and ONNX Runtime have no Intel Mac builds); the install
  uses prebuilt wheels only there, so the rules-only program installs without a compiler.

## 0.4.3 (2026-10-19)
- Windows: the plugin installs Cloakroom on a machine with nothing on it. When neither uv nor pipx is there, it
  fetches uv first, and every install route puts `cloakroom` in `%USERPROFILE%\.local\bin` and adds that folder to
  your user PATH, so `cloakroom` works in a new terminal.
- Windows: the plugin's install adds the Microsoft Visual C++ runtime (the `msvc-runtime` package), and Cloakroom
  finds it before the model libraries load, so a machine without it works, no admin rights needed. If it is still missing,
  `cloakroom doctor` says so in one line instead of printing a traceback, and the installer no longer reports full
  protection when the model can't load.
- The plugin's installs through uv use Python 3.12 on every system (a newer Python had no wheels for the model runtime).
- macOS and Linux: when the plugin installs into its own virtual environment, it links `~/.local/bin/cloakroom`,
  so the command is found like the other routes.
- `scripts/release_check.sh` runs every pre-publish check in order (versions, generated hooks, polyglot, tests, live test, bare-venv suite) and stops at the first failure; `--quick` skips the last two.
- A damaged `~/.cloakroom/rules.bin` no longer crashes every command and hook: your rules are skipped with one
  warning (as with a locked Keychain) and masking runs on the built-in rules.

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
