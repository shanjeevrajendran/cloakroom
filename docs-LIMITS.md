# Known limits (live Claude Code tests 2026-10-09, 2026-10-12 and 2026-10-14, benchmarks through 2026-10-12)

- Prompts can only be blocked, not masked (UserPromptSubmit can't rewrite). A blocked prompt never reaches the
  model, but Claude Code keeps it in its local session records on disk. To limit blocks:
  - a prompt holding only names goes through (you typed them; a name next to an email, phone, key or ID still
    blocks the prompt, as a pasted customer list would). A pasted list of names alone, with nothing else, does go
    through;
  - plain dates go through; only birth / death / age dates and dates in someone's record are caught;
  - your own login name in a home-folder path is never masked (`"mask_own_username": true` to change that);
  - `cr!` at the start of a prompt sends it unchecked, `cr: pause 10m` pauses prompt checks, `cr: resume` ends the
    pause. These are typed by you and never reach Claude; tool output stays masked throughout.
- When the local service can't check (still starting, port held by another program, model failing), tool calls
  are held back, and prompts are checked with the rules alone in-process: a pasted key, card or ID number is still
  blocked, other prompts go through with a warning that they weren't fully checked. On a cold start the first
  check waits up to 40 s for the model to load (`CLOAKROOM_STARTUP_WAIT`) instead of failing.
- Blocked prompts in Claude Code's own output: when Cloakroom blocks a prompt, Claude Code shows its block
  message followed by "Original prompt: ..." with the text you typed, on your screen and in `claude -p` JSON. That
  echo comes from Claude Code, stays local and never reaches the model. Also from Claude Code: a `claude -p` run whose
  prompt was blocked still reports `is_error: false`; check for `num_turns: 0` or the block message in scripts.
- Headless runs (`claude -p`) don't print hook messages, so "installing itself" and "masked ..." lines never show
  there; masking still happens. Check with `cloakroom dashboard` or `~/.cloakroom/hook.log`.
- Restored writes: Claude Code's local session log (`~/.claude/projects/...`) records the hook's rewritten tool
  input, so values put back into a file also appear in that log on your disk. They are not sent to the model.
- Edit: Claude Code validates `old_string` against the file before PreToolUse hooks run, so an `old_string`
  containing a placeholder fails ("string not found"). Placeholders in `new_string` are restored. Cloakroom tells
  Claude (once per session) to anchor `old_string` on placeholder-free text or use Write.
- `updatedInput` replaces the whole tool input in practice, and `updatedToolOutput` must keep the tool result's
  shape (a string for a structured result is ignored). Cloakroom does both; the fail-closed wrapper needs
  `python3` to rebuild the shape.
- Encoded output: `od`, `xxd`, `base64`, spaced-out and truncated dumps are decoded and checked against the
  session's known values (10-character fragments) and the detectors; any hit withholds the whole output. Files a
  Bash command names are scanned first (up to 512 KB). Arbitrary transformations (ROT13, custom ciphers, values
  computed piece by piece) are out of scope: Cloakroom stops accidental leaks, not a model determined to leak.
- Disguised copies (0.6.6) are computed from values Cloakroom already knows, so a secret it has never seen isn't
  caught in any form. Not covered: salted or keyed hashes (HMAC, bcrypt, scrypt), other hashes (SHA-512, CRC),
  base32, hex grouped in fours (`xxd` without `-p`), upper/lower case changes of a key, a name's case changes or
  initials, a card's last 4 without a cue word, an email's local part under the limit above (`jsmith`), parts of
  a key, and values shorter than 8 characters. Reversed only for keys and passwords of 12+ characters.
- PDFs (live test 2026-10-14): Claude Code sends a PDF read to the model as a document (or as page pictures with
  `pages`), which a hook can't mask in place. Cloakroom extracts the text layer locally (pypdf), masks it and gives
  Claude that text instead, so layout, pictures and charts in the PDF are not seen. Pages without a text layer are
  read by local OCR when the `ocr` extra is installed (below), else marked as held back; a PDF with no text at all
  and no OCR, an encrypted or damaged one is held back whole, with a note telling Claude to run `pdftotext file -`
  (masked like any command output) or ask you to paste the text.
- Scans and photos (OCR): with `cloakroom[ocr]`, scanned PDF pages and pictures read with Read go through RapidOCR
  on the CPU (models inside the package, never downloaded at run time; pages rendered with pdfium) and the text is
  masked. Limits: up to `ocr_max_pages` (20) scanned pages per Read, later pages stay held back; one `ocr_timeout`
  (30 s) per Read, after which the read is held back. OCR can misread or split a number, so in OCR text every run of
  10+ digits (spaces and dashes allowed) is masked as an account number; a misread name or email may still slip past
  the detectors, as it would in a typed file with a typo. Handwriting, very small print and photos at an angle read
  poorly. Encrypted PDFs are never rendered. `"ocr": false` turns it off.
- Images: without OCR, Read results that are pictures (png, jpg, gif, webp...) are held back, and figures in
  notebook outputs always are, because Cloakroom can't check pixels. Attach a picture to your own prompt to show it to Claude, or set
  `"allow_images": true` to let them through with a warning.
- MCP images: an image in an MCP tool's result (an `image` block, or an embedded `image/*` resource) is replaced by
  its OCR text, masked like any output, or held back with a one-line reason when OCR isn't there, fails, or finds
  no text. An image block Cloakroom can't decode is held back too. Claude gets text, not the picture: layout and
  anything that isn't text are lost, and small or low-contrast text can be missed. Each image counts against
  `ocr_timeout` on its own. A `resource_link` (a URI with no pixels in the result) is left as it is.
  `"allow_images": true` passes MCP images unchanged, with a warning.
- Claude Code auto-approves read-only commands such as `od`, so permissions alone can't stop a dump.
- Claude's own tool calls can't change `~/.cloakroom` (writes there, or shell commands naming it, are denied), so
  it can't pause Cloakroom or edit its rules. A side effect: Claude can't read Cloakroom's logs with shell commands
  in a protected project either.
- Dates: a date the model finds is masked only near birth, death or age words (English, German, French, Spanish
  forms) or as part of someone's record (next to a name, or in a table / CSV / SQL row holding one). A birth date
  written with neither nearby is missed; `"mask_all_dates": true` masks every date instead.
- Passwords that contain `<...>` (for example `U9dX<!>N4b`) are left as is: they read as template values like
  `<your password>`.
- In files and command output, a lone first name ("Sarah") is masked, which is a common false alarm in support text;
  mark it on the dashboard to turn it into a rule.
- Vault files are AES-GCM encrypted; the key lives in the OS secure store (macOS Keychain, Windows Credential
  Manager, Linux Secret Service), or CLOAKROOM_VAULT_KEY, or a 0600 key file when no store exists (doctor warns).
- A replacement tool result must keep its structural fields (`type`, `filePath`, ...) unchanged: a result Claude
  Code can't parse is dropped and the original passes through. Cloakroom never touches those fields and withholds
  per field.
- If the plugin is installed but the `cloakroom` command isn't on PATH, each hook command (one string valid in both
  bash and PowerShell, idea from maisecrets, Apache-2.0) answers by itself: every tool call is denied before it
  runs, and prompts pass with a warning that gives the install and disable commands, so you're never locked out.
  Checked in CI for bash, Windows PowerShell 5.1 and PowerShell 7.
- Windows: run on a real Windows Server 2025 machine before v0.1 (same benchmark result as macOS, Credential Manager
  vault key); the hooks are covered by CI in bash and both PowerShells. Linux: covered by CI, not yet run on a real
  desktop.
- Speed: each tool call is checked by the local model before Claude sees the result. On Apple silicon (MPS) that's
  about 20 ms per check; on an Apple M-series CPU about 60 ms; on a small 2-core x64 CPU (Windows test VM) about
  0.4 s per check and up to ~0.9 s for a long tool output. So on a modest laptop each tool call may gain under a
  second. `cloakroom doctor` prints your machine's time per check; `CLOAKROOM_MODE=rules` is instant but misses
  names and free-text PII.
- First run downloads the detection model (PII-Tracer, pinned revision) from Hugging Face; offline machines need
  it cached first. The CPU path (ONNX Runtime) needs a one-time export with PyTorch installed (about 4 GB of disk;
  on Windows, PyTorch also needs the Microsoft Visual C++ Redistributable, https://aka.ms/vs/17/release/vc_redist.x64.exe);
  after that PyTorch isn't needed. The int8 export is opt-in: about 95% span agreement with the full model and a
  few more false alarms.
- Left unmasked on purpose (the known-safe filter): numbers reserved for fiction (555-0100..0199, 555-1212),
  toll-free and Twilio test numbers, published test cards and sandbox IDs, documentation example keys and tokens,
  reserved domains (example.com, .test), role and no-reply mailboxes, loopback/private/documentation IP ranges,
  UUIDs and hashes, `$VAR` references, redaction markers, dates over 110 years old and a short list of historical
  figures. Since 0.6.2 also: package versions (`^1.0.5`, `0.76.8`), numbers of up to 6 digits on an UPPER_SNAKE
  constant whose name has no secret word in it (KEY, TOKEN, SECRET, PASS, PWD, AUTH, PIN...), GPG key ids, AWS's
  documentation account and key ids, variable names under a password key (`password: user.password`), XAML resource
  names and ChangeLog file entries. UI text under a key that is a label or a message id (`"incorrectPassword":
  "Wrong user name or password"`) is left as is when it is two or more words with no digits; a password made of
  words under such a key (`"passwordHint": "Lantern Copper Field"`) is not masked either. A real value that happens
  to look like one of these is not masked; add exceptions with `allow`, or report the case.
- False alarms: on fresh test data Cloakroom still masks some safe text (4-6 of 40 tricky negatives in the last two
  one-shot holdouts: landmark addresses, public figures not on the list, office names). On public data, precision is
  0.81 on TAB court rulings and 0.52 on Gretel finance documents, mostly number strings read as account or reference
  numbers. On the CredData public-code set it masks 3,457 harmless look-alikes (22 in 100, down from 3,903 before
  0.6.2), against maisecrets' 997. Masking safe text costs readability, never privacy. Measured and rejected fixes
  (a higher model-confidence bar, a context gate) both leaked real values, so they were not shipped.
- Commit and push check: it reads the staged change (for `git push`, the commits no remote has yet) and lets the
  command run unchecked when the added text is over 4 MB or the check takes over 2 s (a new file over 2 MB is left
  out of the scan); each such skip is logged and told to you once per session, and `cloakroom report` counts them
  under "Checks skipped". A push to a remote with no
  remote-tracking refs scans all history, and runs unchecked if that is slow. Commits made outside Claude Code
  (your own terminal, an editor, another agent) aren't checked.
- Write-time check: a key split into pieces, or one with no known pattern and not seen earlier in the session, isn't
  caught when a file is written. Repos that commit sample keys (fixtures, docs) need `"allow_secret_paths"`, or
  those writes and commits are refused or held.
- Activity log: tamper-evident, not tamper-proof. Someone who rewrites every later line can rebuild the chain, so
  keep the head hash from `cloakroom report --verify` somewhere else. A miss leaves no line, and other agents,
  machines without Cloakroom and prompts sent with `cr!` are not in it.
- `cloakroom clean-history` skips files changed in the last 10 minutes (an open session). It replaces sample keys
  too, not only real ones; the backup folder keeps the originals, and holds the values until you delete it.
- Signed placeholders: placeholders from before 0.6.1 (`PII_EMAIL_1`) are no longer restored, by design. A resumed
  session's old placeholders stay as text until you give the value again. The tag stops guessing, not a program
  that already runs as you and can read the vault key.
- File log: it holds at most 5,000 value positions or 10 MB, and drops the oldest files first; a value in a file
  dropped that way is left to detection again. It only covers files under the session's working folder and the
  file being read, files up to 4 MB, and values of 4 characters or more, matched exactly (a changed case, a part of
  the value or an encoded copy isn't matched). An edit above the value moves it, which ends the entry; values a
  notebook stores with JSON escaping (quotes, line breaks) aren't found in the file and aren't logged.
- The dashboard (`cloakroom dashboard`) is reachable only on 127.0.0.1, with a one-time sign-in link; restarting the
  service signs the browser out. The activity log keeps no values or text, but it does keep file paths (masked like
  any output) and the program name of shell commands.
- Bank numbers: a routing number needs a routing word or field within a few characters and a valid ABA checksum; an
  account number needs an account word, a CSV column of that name, a NACHA entry line, or a routing number in the
  same record. Bare digits, numbers split across lines, non-US account formats other than IBAN, and words in other
  languages aren't caught. A routing number with the right checksum in a field Cloakroom doesn't know stays visible.
- Money-movement hold: it sees the command or tool call, not what a program does once it runs. A script or app
  (`python pay.py`, a test suite, a server Claude starts) that calls a payment API isn't held, nor is a host or MCP
  tool name not on the lists (add them with `"money_hosts"` / `"money_tools"`), a GraphQL payment API, or a URL built
  from variables. A tool call is matched by its name, so a payment tool with a plain name (`run`, `execute`) passes.

## Codex

Checked against Codex CLI 0.164 (hooks docs: https://developers.openai.com/codex/hooks, and its source).

- **Open gap, background output:** a command still running when Codex stops waiting (10 seconds by default; the
  model can choose a shorter or longer wait) returns its output so far to the model, and Codex runs no PostToolUse
  hook on it, nor on the polls that follow until the command ends. That output is not masked. The PreToolUse hook
  only sees the command text, not the wait or whether a terminal is attached, so Cloakroom can't tell such a
  command apart beforehand. `"codex_hold_long_running": true` (off by default in 0.7.1) holds the ones that are
  obviously long-running from their text (servers, `tail -f`, watches, follows, `ping` without a count; `cr: allow`
  lets one run once); each hold is logged as a held event with `"hold": "long-running"`, so false holds can be
  counted. A slow build, a script that starts a server, or a command behind `timeout` isn't held. Avoid those over
  secrets.
- Trust: Codex runs the hooks only after you trust them once in `/hooks`, and again after they change (a reinstall
  from a different program path). Until then it skips them; `codex exec` gives no warning.
- Prompts can only be blocked, not masked, as in Claude Code (Codex's request hook only watches).
- Masked output reaches the model as a blocked tool result holding the masked text: Codex replaces a result only
  through a block (`updatedMCPToolOutput` is read but not applied yet).
- Codex hooks can't ask: a command with placeholders that could send data off the machine, or one Cloakroom
  doesn't know, is denied instead of asked about. Run it yourself if you want it.
- No hook at all: Codex's hosted web search, and pictures from `view_image`. Hooks also stop when they are turned
  off (`[features] hooks = false`) or by an admin policy.
- A missing or broken `cloakroom` program makes tool calls fail closed (the hook exits with code 2 and a reason,
  which Codex treats as a block). Prompts and session starts go on with Codex's error message, so you are never
  locked out.
- Live check 2026-10-24: a `codex exec` run with the hooks installed but not yet trusted skipped them and printed
  no warning. A run with trusted hooks on Codex 0.164 passed three checks: a fake key in Bash output was masked, a
  placeholder in tool input was restored on disk while the model saw only the placeholder, and a missing `cloakroom`
  program blocked the tool call (exit 2). The hook answers are tested against Codex's JSON shapes in
  `tests/test_codex.py`.
- Windows isn't supported yet (`cloakroom install codex` refuses there).


## Git hook (`cloakroom install git-hook`)

- Checks keys and passwords only (the rules and your always-mask values), in the lines a commit adds or the
  commits a push sends that no remote has yet. Personal data isn't checked, as for the agent commit hold.
- Gives up and lets the commit through after 10 seconds or on a change set over 4 MB, with a line saying it wasn't
  checked. If the cloakroom program is gone, the hook says so and lets the commit through.
- One repo per install; no `--global`. A hook file another tool owns (husky, pre-commit) is left alone: add
  `cloakroom git-check commit || exit 1` to it by hand.
- Tested on Mac and Linux. Git for Windows runs the same `sh` hook, but that isn't tested yet.

## Gemini CLI

Checked against Gemini CLI 0.65 (hooks docs: https://geminicli.com/docs/hooks/reference, and its source).

- **Timeouts let the call through:** Gemini treats a hook that runs past its time limit (30 s at session start,
  60 s for prompts and tool input, 120 s for tool output) as a warning and goes on with the original. Cloakroom
  doesn't wait for its model to load: until the local service is ready, checks run on the rules only (keys, cards,
  IDs; names and addresses may be missed), noted once per session and logged as a skipped check. A very slow check (a huge output, many OCR
  pages) can still run past the limit.
- Prompts can only be blocked, not masked: Gemini keeps the original prompt in the conversation and also sends it
  to the model from places no hook sees (model routing, history compression, loop checks). Pictures in a prompt
  (pasted, or `@image.png`) aren't seen by the prompt hook and go unchecked; text from `@file` is checked.
- Masked output reaches the model as a blocked tool result holding the masked text: Gemini replaces a result only
  through a deny.
- No hook sees `GEMINI.md` and other context files (they go into the system instruction), nor what Gemini's own web
  tools fetch on Google's side.
- With LLM edit correction turned on (`"disableLLMCorrection": false`; it is off by default), a `replace` that
  doesn't match sends the file and the restored strings to the model to fix the edit.
- If you set up Gemini's OpenTelemetry export with prompt logging, it logs tool arguments after Cloakroom restored
  them.
- The hooks don't run when the extension is off: `gemini -e` naming other extensions (or `none`),
  `/extensions disable cloakroom`, `"hooksConfig": {"enabled": false}`, `/hooks disable`, or an admin policy
  (turning extensions off, or an `allowedExtensions` list, which needs install records Cloakroom doesn't write).
- A missing or broken `cloakroom` program makes tool calls fail closed (the hook exits with code 2 and a reason,
  which Gemini treats as a block). Prompts and session starts go on with a warning, so you are never locked out.
- Gemini gives hooks no call id, so the file log of where restored values were written isn't kept for Gemini.
- Not yet live-checked against a real Gemini run (it needs a Google sign-in or API key); the hook answers are
  tested against Gemini's JSON shapes in `tests/test_gemini.py`.
- Windows isn't supported yet (`cloakroom install gemini` refuses there).


## Editors

Checked 2026-10-25 against the VS Code extensions of Claude Code (2.1.310), Codex (26.1021, which ships Codex
0.164) and Gemini Code Assist (2.104.0).

- **Claude Code in VS Code:** the extension runs its own copy of Claude Code with your user, project and local
  settings, so the plugin's hooks run as in the terminal. Live check: that copy, started the way the extension
  starts it, masked a fake key and a name in a file's Bash output (all four hooks ran). The JetBrains plugin runs
  `claude` in the IDE's terminal, so nothing changes there.
- **Codex in VS Code:** the extension runs its own Codex (`codex app-server`), which reads `~/.codex/hooks.json`.
  Live check: it listed all four Cloakroom hooks, each "untrusted" until you trust it. Trust them once in the
  `codex` CLI (`/hooks`). A full signed-in session in the extension wasn't run. Every Codex limit above applies,
  the background-output gap included.
- **Gemini Code Assist in VS Code:** its agent mode runs a cut-down Gemini CLI server that loads extensions without
  their hooks and doesn't read hooks from `settings.json` (read from its source). Cloakroom's checks don't run there,
  so nothing is masked. The Gemini CLI in a terminal, with or without the Gemini CLI Companion extension, runs them.
- **Antigravity:** it has hooks of its own (`hooks.json` in `~/.gemini/config/` or `.agents/`), but Cloakroom
  doesn't install any, so it doesn't protect you there.
