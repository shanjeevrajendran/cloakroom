# Benchmark results (2026-10-09, synthetic agent-traffic corpus v1)

112 items: 92 with planted values (530 total: API keys, passwords, names, emails, phones, addresses, SSNs, cards,
IBANs, DOBs, private keys) across env files, JSON configs, app logs, CSV exports, source code, git/env shell output
and notes; 20 clean "looks risky" negatives. A value leaks when it survives in the tool's output; a block counts as
protected but stops the agent.

| Tool | Items leaked | Values leaked | Blocked | False alarms | ms/item |
|---|---|---|---|---|---|
| Cloakroom prototype, rules + PII-Tracer (before the known-safe filter) | 0/92 | 0/530 | 0 | 12/20 | ~41 |
| sensitive-canary (blocks instead of masking) | 5/92 | 20/530 | 87 | 3/20 | 0.5 |
| claude-code-redact, PII on | 75/92 | 207/530 | 0 | 5/20 | 0.1 |
| Cloakroom prototype, rules only | 89/92 | 228/530 | 0 | 7/20 | 0.0 |
| maisecrets | 67/92 | 264/530 | 0 | 2/20 | 0.5 |
| redact-hook (default / aggressive) | 71/92 | 420/530 | 0 | 3/20 | 0.1 |
| claude-code-redact, secrets only (default) | 76/92 | 438/530 | 0 | 0/20 | 0.1 |

Notes
- Competitors run through their own scan/redact functions with default settings, offline (adapters in `adapters/`).
  Reproduce: `bench/setup_competitors.sh` fetches each at the revision benchmarked, then `python bench/run_bench.py`.
- Regex tools catch prefixed API keys well; names, addresses and free-text PII need the model.
- Baseline false alarms come from documented example keys, test cards, public/bot emails, localhost, UUIDs/hashes,
  git remotes and env-var references: the engine's first target.
- Corpus fix during the run: AWS key ids now base32 (A-Z, 2-7) and JWTs valid base64url, so format-checking tools
  are not penalised.
- Before publishing: let each maintainer review their adapter config.

## After the known-safe filter and rules upgrade (engine v0.1)

| Tool | Tuned set: values leaked / false alarms | Holdout: values leaked / false alarms |
|---|---|---|
| Cloakroom full (rules + PII-Tracer) | 0/530, 1/20 | 0/110, 3/15 |
| Cloakroom rules | 0/530, 0/20 | 20/110, 1/15 |

The holdout set (`holdout.jsonl`) was written after tuning; a fresh one is due before publishing.

## Holdout v2 (2026-10-09, run once, no tuning)

120 items written by a different model (Meta's Muse) to a fixed brief, never seen during tuning: 80 with 219
planted values across 30 agent-traffic sources (k8s secrets, Terraform state, Postman, crash reports, Slack
exports, medical intake, non-US IDs, hard cases: split, escaped, URL-encoded, base64, non-Latin names), and
40 negatives built to fool naive tools (published test cards, sandbox IDs, docs example keys, public HQ
addresses, historical people, 555 numbers, localhost). File: `holdout_v2.jsonl`.

| Tool | Items leaked | Values leaked | Blocked | False alarms | ms/item |
|---|---|---|---|---|---|
| Cloakroom full (rules + PII-Tracer) | 6/80 | 6/219 | 0 | 18/40 | 68 |
| sensitive-canary (blocks instead of masking) | 23/80 | 66/219 | 57 | 4/40 | 0.5 |
| maisecrets | 55/80 | 101/219 | 0 | 6/40 | 0.4 |
| Cloakroom rules only | 55/80 | 112/219 | 0 | 7/40 | 0.0 |
| claude-code-redact, PII on | 64/80 | 115/219 | 0 | 10/40 | 0.1 |
| redact-hook (aggressive) | 61/80 | 137/219 | 0 | 8/40 | 0.1 |
| redact-hook (default) | 63/80 | 141/219 | 0 | 8/40 | 0.0 |
| claude-code-redact, secrets only (default) | 65/80 | 172/219 | 0 | 1/40 | 0.1 |

What it shows
- Leaks: all 6 Cloakroom misses are usernames in home-directory paths (`/Users/<name>/`, `C:\Users\<name>`,
  incl. a non-Latin one) and git usernames. No names, emails, keys, cards, IBANs or national IDs leaked.
- False alarms rose from 3/15 (holdout v1) to 18/40: the known-safe filter does not yet know published test
  cards (4242..., 4111..., 5555...4444, Amex 3782...), sandbox IDs (UIDAI 9999..., HMRC QQ 12 34 56 C), 555
  numbers, public HQ addresses, historical people's birth details, loopback URLs, the jwt.io example token,
  `sk-test` keys, "John Doe" and `[REDACTED]` markers. This is the engine's next target, then a fresh holdout.
- Caveat: 2 items (h2-084, h2-091) contain personal-looking emails the brief's author did not list as planted,
  so every tool's leak count is slightly optimistic there. The data was not hand-edited.

### After tuning on holdout v2 (2026-10-09, engine commits 46a1c7f, a0dde3d)
v2 is now a tuning set; these numbers are not a proof. Fixes: home-dir and URL usernames, repeats of a flagged
value, and the known-safe filter (redaction markers, hyphenated test keys, repeated-digit placeholders, test cards
under any label, HMRC QQ, placeholder names, times/expiry/pre-1900 dates, public addresses, loopback URLs, the
jwt.io example, 555-1212). One broader rule (all 555 numbers fictional) was tried and dropped: it leaked 4 planted
phones.

| Set | Cloakroom full: values leaked / false alarms | before |
|---|---|---|
| Tuned corpus v1 (112) | 0/530, 0/20 | 0/530, 1/20 |
| Holdout v1 (75) | 0/110, 2/15 | 0/110, 3/15 |
| Holdout v2 (120, now tuning) | 0/219, 2/40 | 6/219, 18/40 |

Live leak test (`bench/live_test.sh`): 6/6 PASS. Holdout v3 is the next honest score.

## Holdout v3 (2026-10-10, run once, engine a0dde3d, no tuning on this set)

120 new items written by Muse to a fresh brief (new sources: GitHub Actions logs, Helm, Ansible, service-account
JSON, Azure strings, kubectl, HAR, Sentry, Stripe webhooks, FHIR, payroll, HR, travel, PR reviews, mobile crash
logs, shell history, Windows events, lease, court filing), 320 planted values, 40 negatives. File: `holdout_v3.jsonl`.

| Tool | Items leaked | Values leaked | Blocked | False alarms | ms/item |
|---|---|---|---|---|---|
| Cloakroom full (rules + PII-Tracer) | 15/80 | 15/320 | 0 | 12/40 | 66 |
| sensitive-canary (blocks instead of masking) | 6/80 | 18/320 | 74 | 2/40 | 0.5 |
| Cloakroom rules only | 64/80 | 138/320 | 0 | 3/40 | 0.1 |
| maisecrets | 74/80 | 154/320 | 0 | 2/40 | 0.4 |
| claude-code-redact, PII on | 73/80 | 174/320 | 0 | 4/40 | 0.1 |
| redact-hook (aggressive) | 79/80 | 248/320 | 0 | 5/40 | 0.1 |
| redact-hook (default) | 79/80 | 253/320 | 0 | 4/40 | 0.0 |
| claude-code-redact, secrets only (default) | 79/80 | 277/320 | 0 | 0/40 | 0.1 |

What it shows
- Leaks: all 15 Cloakroom misses are phone numbers in the reserved fictional range 555-0100..0199, which the
  known-safe filter skips on purpose (it predates this run). Strict score 15/320; counting only numbers that could
  be real, 0/305. No names, emails, keys, cards, IBANs or national IDs leaked. The next brief should keep planted
  phones out of the reserved range.
- False alarms (12/40): famous people and their dates (model), landmark addresses, documentation IP ranges
  (RFC 5737), `***MASKED***`, maintenance-window times, 555-01xx in parentheses, sample account numbers, a cut-off
  example Google key.
- Data check: all planted values exact; 6 flagged look-alikes were connection-string fragments and doc examples.

### After tuning on holdout v3 (2026-10-10)
v3 is now a tuning set. Known-safe filter, round two: redaction markers with stars (`***MASKED***`), time ranges,
"dates" with no digits or no year, dates over 110 years old, sequential-digit placeholders, documentation IP
ranges (RFC 5737/3849), truncated or prefix-only keys, a short list of historical figures (whole name only, a
model span cut inside a word is judged on the whole words). Leaks unchanged on every set.

| Set | Cloakroom full: values leaked / false alarms | before |
|---|---|---|
| Tuned corpus v1 | 0/530, 0/20 | 0/530, 0/20 |
| Holdout v1 | 0/110, 2/15 | 0/110, 2/15 |
| Holdout v2 | 0/219, 2/40 | 0/219, 2/40 |
| Holdout v3 (now tuning) | 15/320\*, 2/40 | 15/320\*, 12/40 |

\* the reserved fictional 555-01xx phones. Left as false alarms on purpose: landmark addresses (no general rule
short of a gazetteer) and `(555) 010-0199` (area code 555 is also used as a stand-in for real numbers).

## Holdout v4 (2026-10-10, run once, engine a9d5b77, no tuning on this set)

120 new items written by Muse to a fresh brief (Docker/nginx/Postgres logs, Terraform plans, CloudFormation,
ConfigMaps, alert payloads, Jira/Zendesk exports, ICS/vCard, bank statement, insurance claim, prescription, git
blame, Kafka payloads...), 328 planted values, 40 negatives; US phones outside the reserved 555 range.
File: `holdout_v4.jsonl`.

| Tool | Items leaked | Values leaked | Blocked | False alarms | ms/item |
|---|---|---|---|---|---|
| Cloakroom full (rules + PII-Tracer) | 4/80 | 4/328 | 0 | 12/40 | 57 |
| sensitive-canary (blocks instead of masking) | 10/80 | 36/328 | 70 | 2/40 | 0.4 |
| Cloakroom rules only | 71/80 | 163/328 | 0 | 4/40 | 0.1 |
| maisecrets | 77/80 | 181/328 | 0 | 3/40 | 0.3 |
| claude-code-redact, PII on | 76/80 | 204/328 | 0 | 5/40 | 0.1 |
| redact-hook (aggressive) | 80/80 | 254/328 | 0 | 8/40 | 0.1 |
| redact-hook (default) | 80/80 | 255/328 | 0 | 6/40 | 0.0 |
| claude-code-redact, secrets only (default) | 80/80 | 304/328 | 0 | 0/40 | 0.1 |

What it shows
- Leaks (4): a public IP followed by a port (the "code location" check took `IP:port` for `file:line`), a username
  in an nginx access log, a username in a Terraform resource name, a Twilio account SID (no rule for its shape).
- False alarms (12/40), mostly new kinds: the word `Bearer` read as a key, a `$VAR` reference, landmark and
  venue addresses, a Japanese postcode, a public figure not on the list, a UUID cut short, an ISO date, a
  descending-digit placeholder, Twilio's magic test number, an 800 number, a stray "30 3".
- Round-two rules mostly held, but false alarms did not generalise: they fell to 2/40 on v3 (tuning) and are
  12/40 on fresh data. The famous-people list missed the first public figure it had not seen.

### After fixing the v4 leak classes (2026-10-10)
v4 is now a tuning set. New rules: Twilio account/API-key SIDs, SendGrid keys, the remote user in access logs,
IAM user names in infrastructure code; the code-location check no longer takes `IP:port` for `file:line`.
Cloakroom full on v4: 0/328 leaked (was 4/328), false alarms unchanged at 12/40. No change on the other sets.

### False alarms, round three (2026-10-10, measured first)
Measured every model finding on all five sets (2,134) before changing anything:
- The model's document-sensitivity score does not separate real values from false alarms (both near 0).
- No confidence bar helps: real values score down to 0.959 while false alarms sit at 0.95-0.97. A 0.97 bar was
  tried and leaked 2 real values on v3, so it was rolled back.
- A context gate (keep model addresses/dates only near cues like "address", "DOB", an email or phone) would cut
  the most false alarms but leaked 3 real values on the tuning sets (non-English labels, cues too far away). Not
  shipped: it needs per-language cue lists and fails by leaking.
Shipped (safe parts): `Bearer`/`Basic`/`Token` scheme words are not taken as the key, `$VAR`/`${VAR}`/`%VAR%`
references, toll-free numbers, Twilio test numbers, repeated-group digit placeholders, UUID fragments labelled as
account numbers, "dates" like `30 3`. v4 (now tuning) false alarms 12/40 -> 6/40; leaks unchanged on every set.

## Holdout v5 (2026-10-10, run once, engine 4fd0cc2, no tuning on this set)

120 new items written by Muse (Slack/Teams exports, .eml, CRM exports, dispute evidence, Shopify orders, webhook
logs, Okta/Auth0 audit logs, VPN/SSH auth logs, GitLab CI, Ansible/Chef, SQLite dumps, HR rosters, tax return,
mortgage and visa applications, discharge summary, report card, dating-app export, gym form, Airflow/dbt logs),
394 planted values, 40 negatives; no planted value reused from earlier sets. File: `holdout_v5.jsonl`.

| Tool | Items leaked | Values leaked | Blocked | False alarms | ms/item |
|---|---|---|---|---|---|
| Cloakroom full (rules + PII-Tracer) | 2/80 | 2/394 | 0 | 6/40 | 63 |
| sensitive-canary (blocks instead of masking) | 1/80 | 5/394 | 79 | 3/40 | 0.5 |
| Cloakroom rules only | 71/80 | 184/394 | 0 | 1/40 | 0.1 |
| maisecrets | 78/80 | 203/394 | 0 | 5/40 | 0.4 |
| claude-code-redact, PII on | 79/80 | 238/394 | 0 | 5/40 | 0.1 |
| redact-hook (aggressive) | 80/80 | 308/394 | 0 | 11/40 | 0.1 |
| redact-hook (default) | 80/80 | 309/394 | 0 | 10/40 | 0.1 |
| claude-code-redact, secrets only (default) | 80/80 | 372/394 | 0 | 0/40 | 0.1 |

What it shows
- Leaks (2): an SSH key fingerprint (`SHA256:...`) and a database user name in a dbt log line.
- False alarms (6/40): a documentation-style Okta token, a landmark address, a historical birth date (1918, under
  the 110-year cut), a stray "arn", a "Building 43" office, a "123456" sample code.
- Against the goal set on 2026-10-10 (0 leaks, <= 4/40 false alarms on a fresh set): missed on both, narrowly.
  False alarms fell from 12/40 (v4, fresh) to 6/40 (v5, fresh).

### Final tuning round on holdout v5 (2026-10-10)
v5 is now a tuning set. Leak rules: SSH key fingerprints (`SHA256:`/`MD5:`), users in sshd auth lines, the user on
a database/host line. Safe rules: alphabet-run and counting placeholders flagged only by the model (a keyed
`password: 123456` stays masked), lowercase 1-3 letter "names". v5: 2/394 -> 0/394 leaked, 6/40 -> 3/40 false
alarms; v4 false alarms 6/40 -> 5/40; no change elsewhere. Holdout v6 is the release score.

### CPU backends: PyTorch vs ONNX Runtime (2026-10-10)
Linux, 4-core Xeon @ 2.1 GHz with AMX-BF16 (so PyTorch's bf16 encoder is unusually fast here), torch 2.14.1,
onnxruntime 1.31.0. `torch-CPU` is the PyTorch backend as it runs on CPU (bf16 encoder, the checkpoint dtype).
`onnx-int8` = per-channel dynamic int8 weights; token head and every MLP `down_proj` kept fp32 (activation outliers).

| Set | Backend | Values leaked | False alarms | ms/item, model only | ms/item, bench |
|---|---|---|---|---|---|
| Corpus v1 (112) | torch-CPU | 0/530 | 1/20 | 231 | 320 |
| | onnx-fp32 | 0/530 | 0/20 | 366 | 430 |
| | onnx-int8 | 0/530 | 1/20 | 159 | 287 |
| Holdout v1 (75) | torch-CPU | 0/110 | 2/15 | 125 | 222 |
| | onnx-fp32 | 0/110 | 2/15 | 160 | 214 |
| | onnx-int8 | 0/110 | 1/15 | 79 | 169 |
| Holdout v2 (120) | torch-CPU | 0/219 | 2/40 | 184 | 238 |
| | onnx-fp32 | 0/219 | 2/40 | 284 | 305 |
| | onnx-int8 | 0/219 | 5/40 | 145 | 202 |

"Model only" times `predict()` with the model loaded (`bench/onnx_parity.py`); "bench" is `run_bench.py`, which
includes rules, masking and the model load in the first item.

Span parity (same label, start, end; `bench/*_onnx_parity.json` lists every difference):

| Set | onnx-fp32 vs torch-fp32 | onnx-fp32 vs torch-CPU | onnx-int8 vs torch-CPU |
|---|---|---|---|
| Corpus v1 | 100.00% | 97.56% (= torch-fp32 vs torch-CPU) | 94.91% |
| Holdout v1 | 100.00% | 100.00% | 97.79% |
| Holdout v2 | 100.00% | 98.75% | 92.75% |

The ONNX export and the NumPy decoder reproduce PyTorch exactly in fp32; the gap to torch-CPU is bf16 vs fp32.
int8 differences are mostly span edges (`KEY=` included or not, a trailing `")`), adjacent secrets merged or split,
and label swaps on CSV row ids (`account_number` vs `private_person`). None leaks a value: rules and the
known-safe filter decide the final mask. Quantization variants tried on the corpus (parity vs onnx-fp32 / ms per item):
per-tensor 65% / 120, per-channel 75% / 122, per-channel with outlier layers 10, 19, 22, 23, 27 fp32 89% / 152,
all `down_proj` fp32 94% / 164 (chosen), SmoothQuant (alpha 0.5) + all `down_proj` fp32 95% / 161,
8-bit block-wise weight-only (MatMulNBits) 98% / 273-358.

### CPU backends measured on an Apple M-series CPU (2026-10-10)
The cloud machine above has AMX-BF16, which flatters PyTorch on CPU. On a CPU without bf16 support (Apple M-series
CPU here, like most laptops and the Windows CI runners), 40 holdout-v5 items, model only, after warm-up:

| Backend | ms/item |
|---|---|
| PyTorch on the Apple GPU (MPS, the default on a Mac) | 20 |
| PyTorch on CPU (bf16 checkpoint) | 555 |
| **ONNX Runtime fp32 on CPU (new CPU default)** | **62** |
| ONNX Runtime int8 on CPU | 55 |

ONNX fp32 on all six sets: leaks identical to PyTorch (0 everywhere; v3's 15 are the reserved 555-01xx phones),
false alarms identical except v5 (4/40 vs 3/40, one borderline span). int8 is opt-in (`CLOAKROOM_ONNX=int8`).

## Holdout v6 = release score (2026-10-10, run once, engine 4651d4d, no tuning on this set)

120 new items written by Muse (Notion/Confluence exports, Docs/Figma comments, Trello/Asana, Discord and
WhatsApp exports, call-center and voicemail transcripts, Kubernetes/CloudTrail/GCP/Azure audit logs, Vault/1Password
output, install logs, Dockerfiles, deploy logs, Firebase/Redis/warehouse output, payroll slips, utility bills,
rental and immigration paperwork, clinical case report forms), 314 planted values, 40 negatives; no planted value
reused from earlier sets. 34 lines arrived with over-escaped quotes (`\\"`) and were repaired uniformly by the
importer (`--repair-escapes`); every planted value still matches its text exactly. File: `holdout_v6.jsonl`.

| Tool | Items leaked | Values leaked | Blocked | False alarms | ms/item |
|---|---|---|---|---|---|
| Cloakroom full (rules + PII-Tracer) | 3/80 | 3/314 | 0 | 12/40 | 63 |
| sensitive-canary (blocks instead of masking) | 18/80 | 65/314 | 62 | 5/40 | 0.5 |
| Cloakroom rules only | 66/80 | 175/314 | 0 | 2/40 | 0.1 |
| maisecrets | 71/80 | 176/314 | 0 | 5/40 | 0.4 |
| claude-code-redact, PII on | 73/80 | 194/314 | 0 | 7/40 | 0.1 |
| redact-hook (aggressive) | 77/80 | 232/314 | 0 | 8/40 | 0.1 |
| redact-hook (default) | 79/80 | 242/314 | 0 | 7/40 | 0.1 |
| claude-code-redact, secrets only (default) | 76/80 | 278/314 | 0 | 1/40 | 0.1 |

What it shows (accepted as the v0.1 result; no tuning round after it)
- Leaks (3): a utility-bill account ID and a driver's licence number in formats no rule covers, and the user
  part of a cloud audit log's principal email.
- False alarms (12/40): historical people not on the short list, GPS coordinates read as an address, a landmark
  address, Stripe's documented test key and sandbox keys, an all-zero Twilio SID, ticket and serial numbers, the
  1970-01-01 epoch date, short digit fragments. Masking safe text costs readability, not privacy.
- Across the one-shot holdouts (v2-v6) Cloakroom leaked 6, 15 (all reserved fictional 555 numbers), 4, 2 and 3
  values; false alarms 18, 12, 12, 6 and 12 of 40.

## Leaks by how much they matter (holdouts v2-v6, first runs)

Generated by `python bench/tiers.py` (the tier rules are in its docstring; tiers come from value shapes, so a
few values may sit in a debatable tier, and ambiguous shapes lean critical). 1,575 planted values across five
fresh sets, each run once:

| Tool | Critical | High | Low | Fictional (reserved 555-01xx) |
|---|---|---|---|---|
| cloakroom full | 4/334 | 0/1005 | 11/221 | 15/15 |
| sensitive-canary (blocks) | 40/334 | 119/1005 | 31/221 | 0/15 |
| cloakroom rules | 96/334 | 468/1005 | 193/221 | 15/15 |
| maisecrets | 97/334 | 557/1005 | 161/221 | 0/15 |
| claude-code-redact (+pii) | 101/334 | 607/1005 | 217/221 | 0/15 |
| redact-hook (aggressive) | 143/334 | 817/1005 | 219/221 | 0/15 |
| redact-hook (default) | 157/334 | 822/1005 | 221/221 | 0/15 |
| claude-code-redact (secrets) | 179/334 | 992/1005 | 217/221 | 15/15 |

Cloakroom's 4 critical misses: a Twilio account SID (v4), an SSH key fingerprint (v5, arguably low), a utility-bill
account ID and a driver's licence number (v6). It never missed a name, email, phone number, address or date of
birth. Its 11 low misses are usernames in paths, logs and git config, and one public IP. Some tools target secrets
only by design (claude-code-redact's default, redact-hook), so their high-tier numbers measure scope, not quality.

### Windows (Azure Standard_D2s_v4, 2 vCPU, Windows Server 2025, 2026-10-10)
Real Windows machine, ONNX fp32 backend (exported on the VM), holdout v6: **3/314 values leaked, 12/40 false
alarms**, identical to macOS. Speed: `cloakroom doctor` one check 412 ms; v6 average 887 ms per item on this
2-vCPU server CPU (62 ms on an Apple M-series CPU). Vault key in Windows Credential Manager. The test found two
Windows bugs, both fixed: a PyTorch that is installed but can't load (no VC++ runtime) crashed auto mode instead of
falling back to ONNX (d893fe4); `cloakroom mask` crashed printing to a legacy cp1252 console (7b7a233).
