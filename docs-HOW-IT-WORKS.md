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
