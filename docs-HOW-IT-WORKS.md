# How Cloakroom works

- **Hide:** hooks on every tool input and tool output run the detectors (a prompt with sensitive data is
  blocked instead, since prompts can't be rewritten): pattern rules for secrets with
  known shapes (AWS, GitHub, Stripe, Slack, OpenAI, Google, JWTs, private keys, keyed passwords), checksummed PII
  (cards, IBANs, SSNs), context rules (names next to emails, addresses, birth dates, home-directory usernames),
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
- **Encoded dumps:** `od`, `xxd`, `base64` and spaced-out output are decoded and checked; a hit withholds the output.
- **Encoded secrets:** each secret already in the session vault (keys, passwords, card, account and ID numbers) and
  each exact always-mask value is also looked for in its encoded forms: base64 (standard and URL-safe, with or
  without padding, also inside a longer encoded string such as `user:secret`), URL-encoded and JSON-escaped. These
  are made from the known values, not guessed from the text, so ordinary text can't trip them. Prompts, tool inputs
  and tool output all get this check; an encoded copy gets its own placeholder, so restoring puts it back as it was.
- **Egress hold:** before a shell command runs, Cloakroom checks whether it can send data off the machine (curl,
  wget, httpie, nc, telnet, ssh, scp, sftp, rsync, ftp, mail, cloud and database clients, `git push`/`fetch`/`clone`/
  `remote`, `npm publish`, `docker push`/`login`, `/dev/tcp`, PowerShell web calls) or carries a credential header
  (`Authorization:`, `X-Api-Key:`, `token=`). If it does and it holds a known secret (plain or encoded), a key the
  rules recognise, or a literal token of 16+ characters in such a header, the command is denied. The reason names
  the placeholder or the rule, never the value. A variable (`$TOKEN`) or a placeholder passes as before.
  To send it anyway, type `cr: allow` as your prompt (Claude never sees it), then ask Claude to run the same command
  again: it passes once, within 10 minutes. A changed command is held again. Only a keyed hash of the held command
  is stored (`~/.cloakroom/held.json`).
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
