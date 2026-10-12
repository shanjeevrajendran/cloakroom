# Known limits (v0.2; live Claude Code tests 2026-10-09 and 2026-10-12, benchmarks through 2026-10-12)

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
  figures. A real value that happens to look like one of these is not masked; add exceptions with `allow`, or
  report the case.
- False alarms: on fresh test data Cloakroom still masks some safe text (4-6 of 40 tricky negatives in the last
  two one-shot holdouts: landmark addresses, public figures not on the list, office names). On public data,
  precision is 0.81 on TAB court rulings and 0.52 on Gretel finance documents, mostly number strings read as account
  or reference numbers. Masking safe text costs readability, never privacy. Measured and rejected fixes (a higher
  model-confidence bar, a context gate) both leaked real values, so they were not shipped.
- The dashboard (`cloakroom dashboard`) is reachable only on 127.0.0.1, with a one-time sign-in link; restarting the
  service signs the browser out. The activity log keeps no values or text, but it does keep file paths (masked like
  any output) and the program name of shell commands.
