# How Cloakroom works

- **Hide:** hooks on every tool input and tool output run the detectors (a prompt with sensitive data is
  blocked instead, since prompts can't be rewritten): pattern rules for secrets with
  known shapes (AWS, GitHub, Stripe, Slack, OpenAI, Google, JWTs, private keys, keyed passwords), checksummed PII
  (cards, IBANs, SSNs, US routing numbers next to a routing word), context rules (names next to emails, addresses,
  birth dates, home-directory usernames, bank account numbers next to an account word or a routing number),
  and the PII-Tracer model for names, addresses and other free-text personal data. A value found once is masked
  everywhere it repeats.
- **Filter:** a known-safe filter leaves documented examples alone: published test cards and keys, sandbox IDs,
  reserved domains, bot and role mailboxes, loopback addresses, UUIDs and hashes, redaction markers, filler
  placeholders (`AC000…`, `sk-...`), standard numbers after RFC / ISO / ICAO / IEEE / PEP, and default dates
  (1970-01-01, 1900-01-01).
- **Pictures:** PDFs become their text layer; scans, Read pictures and images in MCP tool results are read by local
  OCR (the `ocr` extra) and only the masked text goes to Claude. Without OCR they are held back.
- **Restore:** values go into an encrypted per-session vault (AES-GCM, key in the OS secure store). When Claude
  writes a file or runs a command with a placeholder, the real value is put back locally. The model never sees it.
- **Signed placeholders:** a placeholder is `PII_<KIND>_<n>_<tag>`. The tag is 6 characters (30 bits) of an HMAC
  over the vault's random id, the session and `PII_<KIND>_<n>`, keyed by a key derived from the vault key (never
  stored). A value comes back only when the tag checks out and the placeholder is in this session's vault. Anything
  else in placeholder shape (a guessed number, a wrong tag, another session's placeholder, the untagged form from
  before 0.6.1) stays as text; Claude is told which were left, and the activity log records a refused restore with
  its reason and no values. Three refused guesses in one session show you one line. Detection never masks text that
  is already a placeholder, in either form.
- **File log:** when real values are put back into a file (Write, Edit, MultiEdit, NotebookEdit, or files a local
  shell command names), the hook notes the placeholders and paths before the tool runs, then, once it has run,
  finds the values in the file and records per file its path (one spelling per file: symlinks resolved; on Windows
  lower case, forward slashes, no `\\?\` prefix), its SHA-256, size and time, and per value the byte position,
  the value's SHA-256, kind and placeholder. The log (`~/.cloakroom/filelog.bin`) is encrypted like the vault and
  holds no values: they are read back from the file when needed. Before detection runs on later output, the entries
  for files under the session's working folder (and the file being read) give their values as exact matches, so they
  are masked even where detection would miss them: the same placeholder in the same session, the session's own
  placeholder in a new one. An entry stays while the file has the same hash or the position still holds the same
  value; a deleted or changed file drops it, checked on every read and every new entry.
- **Encoded dumps:** `od`, `xxd`, `base64` and spaced-out output are decoded and checked; a hit withholds the output.
- **Encoded secrets:** each secret already in the session vault (keys, passwords, card, account and ID numbers) and
  each exact always-mask value is also looked for in its encoded forms: base64 (standard and URL-safe, with or
  without padding, also inside a longer encoded string such as `user:secret`), URL-encoded and JSON-escaped. These
  are made from the known values, not guessed from the text, so ordinary text can't trip them. Prompts, tool inputs
  and tool output all get this check; an encoded copy gets its own placeholder, so restoring puts it back as it was.
- **Disguised secrets (0.6.6):** the same known values are also looked for as an MD5, SHA-1 or SHA-256 hash (hex,
  lower or upper case), as hex of their bytes (`6b39...`, `0x...`, `6b 39 ...`, `6b:39:...`, `\x6b\x39...`, also
  inside a longer hex run such as hex of `user:secret`), and keys and passwords of 12+ characters reversed. A
  known email is matched only as a hash (of the email as typed and trimmed and lowercased), since the email rule
  already finds it in any case. Two parts count, only where the text points at them: a known card or account
  number's last 4 digits right after "ending in", "last 4", `****`, `xxxx` or `••••`, and a known email's local
  part as a word of its own when it can't be an ordinary word (6+ characters, not a role name like `support`,
  with a digit or `. _ -` unless 8+ long, never this machine's own account name). A hash counts only as a whole,
  not inside a longer hex run. Each copy keeps its source's kind (an email's hash is `PII_EMAIL_n`) with its own
  placeholder, so restore puts back the exact hash, hex or part. Copies are never used as seeds for further
  copies, so the list of forms stays the same size turn after turn. The egress hold looks for the same known
  values as before; an email's hash in a URL doesn't hold a command.
- **Egress hold:** before a shell command runs, Cloakroom checks whether it can send data off the machine (curl,
  wget, httpie, nc, telnet, ssh, scp, sftp, rsync, ftp, mail, cloud and database clients, `git push`/`fetch`/`clone`/
  `remote`, `npm publish`, `docker push`/`login`, `/dev/tcp`, PowerShell web calls) or carries a credential header
  (`Authorization:`, `X-Api-Key:`, `token=`). If it does and it holds a known secret (plain or encoded), a key the
  rules recognise, or a literal token of 16+ characters in such a header, the command is denied. The reason names
  the placeholder or the rule, never the value. A variable (`$TOKEN`) or a placeholder passes as before.
  To send it anyway, type `cr: allow` as your prompt (Claude never sees it), then ask Claude to run the same command
  again: it passes once, within 10 minutes. A changed command is held again. Only a keyed hash of the held command
  is stored (`~/.cloakroom/held.json`).
- **Money-movement hold:** before any tool call runs, Cloakroom checks whether it would move money: a shell command
  that writes (`-X POST`/`PUT`/`PATCH`, `-d`, `--json`, `-F`, PowerShell `-Method Post` or `-Body`) to a listed
  payment endpoint (Stripe payouts, transfers, charges, payment intents and refunds; Plaid transfer and payment
  initiation; Dwolla, Wise, Adyen, PayPal, Square, Increase, Modern Treasury and Column payment or transfer paths),
  a payment CLI command (`stripe payouts create`), an MCP tool whose name carries a money verb (`send_money`,
  `create_payout`, `initiate_transfer`, `pay_invoice`), or a tool call with a POST, PUT or PATCH `method` to a
  listed endpoint. Such a call is held with one line naming the endpoint or tool; `cr: allow` lets the same call run
  once, as with the egress hold, and a call that trips both holds shows one message and needs one `cr: allow`. The
  lists live in `src/cloakroom/money.py`; `"money_hosts"` and `"money_tools"` in `~/.cloakroom/config.json` add to
  them, and `"money_hold": false` turns the hold off. Holds and allows are logged with the endpoint or tool name,
  never the request body.
- **Write-time check:** when Claude writes a file (Write, Edit, MultiEdit, NotebookEdit, or a shell `>`, `>>` or
  `tee`) and a key or password would land in a file that is in a git repo and not git-ignored, the call is denied
  and Claude is told to put the value in `.env` and read it from the environment. `.env*`, `.envrc`, `*.pem`,
  `*.key`, `*.p12`, `credentials*`, ignored files and files outside a repo are written as before. For fixtures and
  docs that hold sample keys, list the paths in `"allow_secret_paths"` in `~/.cloakroom/config.json`. If git
  doesn't answer within a second, the write goes through.
- **Commit and push check:** before Claude runs `git commit` or `git push`, Cloakroom reads what would be committed
  (with `-a`, paths or a `git add` in the same command: the working tree too) or the commits no remote has yet. A
  key or password there holds the command like the egress hold: one line names the file and the placeholder or
  kind. Type `cr: allow`, then ask Claude to run the same command again, and it passes once. Sample keys and
  `allow_secret_paths` pass. A git error, a very large change or a slow check lets the command run.
- **Network one-liners:** `python -c`, `node -e`, `ruby -e`, `perl -e`, `php -r`, `deno eval` and `bash -c` count
  as network commands when their code can open a connection. Real values aren't restored into them, and a known
  secret in one holds the command, as for curl.
- **Agent config files:** a live token written into `.mcp.json`, `.claude/settings.json`, `.cursor/mcp.json` or
  `claude_desktop_config.json` inside a repo (and not git-ignored) is denied. Claude is told to use an environment
  reference such as `${GITHUB_TOKEN}` instead.
- **Activity log:** each line of the event log carries the hash of the line before it, one file per month, kept
  `"log_months"` months (default 12). `cloakroom report [--since DATE] [--format csv|json]` gives counts per day and
  session by data kind and action, with no values (the dashboard's Export button gives the same CSV).
  `cloakroom report --verify` checks the chain and prints the head hash; keep that hash somewhere else.
- **Old transcripts:** `cloakroom clean-history [--dry-run]` finds keys and passwords in old Claude Code
  transcripts and your prompt history. The first run only counts, per project; on your yes it replaces them and
  keeps a backup folder, which you delete when you're sure.
- **Fail closed:** if the `cloakroom` command is missing, the plugin blocks prompts and denies tool calls instead
  of letting data through.

Useful commands: `cloakroom check FILE` (what would be masked, no values printed), `cloakroom explain FILE` (each
finding with its rule), `cloakroom status`. Add your own exceptions to `allow` in `~/.cloakroom/config.json`
(exact values, or `re:<regex>`).

## Threat model

Cloakroom stops **accidental** exposure: secrets and personal data in files, logs and command output an agent
reads while working. It is not a sandbox. Out of scope: a model or prompt injection determined to exfiltrate data
through custom encodings or values built piece by piece; data you paste into prompts on purpose (prompts can be
blocked, not rewritten); what other tools on your machine send elsewhere. Details: [docs-LIMITS.md](docs-LIMITS.md).
