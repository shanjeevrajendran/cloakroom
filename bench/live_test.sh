#!/usr/bin/env bash
# Live leak test: one real Claude Code session (your own login) with the Cloakroom plugin on fake data.
# Checks that real values never reach the transcript, that writes get real values back, and that the vault is
# not plaintext. Uses one short `claude -p` run of your plan's usage.
#
#   bench/live_test.sh            run, print PASS/FAIL per check, exit 1 on any failure
#   bench/live_test.sh --keep     keep the temp folder for inspection
#   bench/live_test.sh --control  negative control: same run without the plugin; the leak checks must FAIL
#
# Env: CLOAKROOM_PORT (default 8797, kept off the default port so a running service is untouched).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
KEEP=0; CONTROL=0
for a in "$@"; do case "$a" in --keep) KEEP=1;; --control) CONTROL=1;; *) echo "unknown option $a"; exit 2;; esac; done
PLUGIN=(--plugin-dir "$REPO/plugin"); [[ $CONTROL == 1 ]] && PLUGIN=()
command -v claude >/dev/null || { echo "claude CLI not on PATH"; exit 2; }
[[ -x "$REPO/.venv/bin/cloakroom" ]] || { echo "missing $REPO/.venv/bin/cloakroom (uv sync first)"; exit 2; }

W="$(mktemp -d "${TMPDIR:-/tmp}/cloakroom-live.XXXXXX")"
export CLOAKROOM_HOME="$W/home" CLOAKROOM_PORT="${CLOAKROOM_PORT:-8797}" PATH="$REPO/.venv/bin:$PATH"
unset CLOAKROOM_BIN

stop_service() {  # only the service on our test port
  local pids; pids="$(lsof -ti "tcp:$CLOAKROOM_PORT" -sTCP:LISTEN 2>/dev/null || true)"
  [[ -n "$pids" ]] && kill $pids 2>/dev/null || true
}
cleanup() { stop_service; if [[ $KEEP == 1 ]]; then echo "kept: $W"; else rm -rf "$W"; fi; }
trap cleanup EXIT
stop_service

# Fake fixtures (the run-10 scenario).
mkdir -p "$W/proj"
printf 'ADMIN_EMAIL = "henrik.dahl@quillfield.com"\nSTRIPE_KEY = "sk_live_9fQ2xLmV7tR4pW8nK3jH6bYd"\nOWNER = "Henrik Dahl"\n' > "$W/proj/config.py"
printf 'name,email,phone\nNadia Petrov,npetrov@marrowgate.io,(415) 555-2381\nCormac Doyle,cdoyle@tessellate.org,(312) 555-7710\n' > "$W/proj/customers.csv"
# A one-page bank statement PDF (text layer), written by hand so no PDF tool is needed.
"$REPO/.venv/bin/python" - "$W/proj/statement.pdf" <<'PDF'
import sys
lines = ["Brightwater Bank - Monthly Statement", "Account holder: Henrik Dahl",
         "Account number: 4417 2290 5531 8820", "Email: henrik.dahl@quillfield.com"]
ops = "".join(f"BT /F1 12 Tf 72 {720 - 18 * i} Td ({t}) Tj ET\n" for i, t in enumerate(lines))
objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [5 0 R] /Count 1 >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", f"<< /Length {len(ops)} >>\nstream\n{ops}endstream",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents 4 0 R >>"]
out, offs = b"%PDF-1.4\n", []
for i, o in enumerate(objs, 1):
    offs.append(len(out)); out += f"{i} 0 obj\n{o}\nendobj\n".encode()
x = len(out)
out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
open(sys.argv[1], "wb").write(out)
PDF

PROMPT="This is an approved test of a local masking tool on fake data. Read config.py and customers.csv. Create notes.md with one line: 'Owner: <owner as you see it>, contact <admin email as you see it>'. Run grep -n with the admin email as you see it on config.py. Then run od -c config.py | head -5 and say only whether its output was readable or withheld. Read statement.pdf and say only whether its account holder came through as a placeholder. Do not quote values."

[[ $CONTROL == 1 ]] && echo "CONTROL run: no plugin, expect FAIL on the leak checks"
echo "running claude -p in $W/proj (port $CLOAKROOM_PORT) ..."
(cd "$W/proj" && claude -p "$PROMPT" ${PLUGIN[@]+"${PLUGIN[@]}"} \
   --allowedTools "Read,Write,Bash(grep:*),Bash(od:*),Bash(head:*)" \
   --output-format json < /dev/null > "$W/run.json" 2> "$W/run.err") || true

python3 - "$W" <<'EOF'
import glob, json, os, re, sys
W = sys.argv[1]
home = os.environ["CLOAKROOM_HOME"]
vals = ['henrik.dahl@quillfield.com', 'sk_live_9fQ2xLmV7tR4pW8nK3jH6bYd', 'Henrik Dahl',
        'npetrov@marrowgate.io', 'Nadia Petrov', 'cdoyle@tessellate.org', 'Cormac Doyle', '555-2381', '555-7710',
        '4417 2290 5531 8820']
sq = lambda s: re.sub(r'\s+', '', s)
def frag(v, t):  # any 8-char window of a value (or the whole value if shorter)
    v = sq(v); n = min(8, len(v))
    return any(v[i:i + n] in t for i in range(len(v) - n + 1))

results = []
def check(name, ok, detail=""):
    results.append(ok); print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))

try:
    run = json.load(open(f"{W}/run.json"))
except Exception as e:
    print("FAIL  claude run produced no JSON:", e)
    print(open(f"{W}/run.err").read()[-800:]); sys.exit(1)
check("claude run finished", not run.get("is_error"), run.get("subtype", ""))

log = f"{home}/hook.log"
check("hooks fired", os.path.exists(log) and os.path.getsize(log) > 0)

tx = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{run.get('session_id')}.jsonl"))
if tx:
    raw = open(tx[0]).read()
    # the PDF must reach Claude as masked text (not as a document block, which no hook can check)
    check("PDF read as masked text", "Account holder: PII_PERSON_" in raw and "JVBER" not in raw)
    n = 0
    for line in open(tx[0]):
        for b in (json.loads(line).get("message") or {}).get("content") or []:
            if isinstance(b, dict):
                t = sq(json.dumps(b).replace('\\n', '').replace('\\\\n', ''))
                n += any(frag(v, t) for v in vals)
    check("transcript has no real values", n == 0, f"{n} blocks with an 8-char fragment")
else:
    check("transcript found", False, "no session file")

result = run.get("result") or ""
check("final answer has no real values", not any(frag(v, sq(result)) for v in vals))

notes = open(f"{W}/proj/notes.md").read() if os.path.exists(f"{W}/proj/notes.md") else ""
check("notes.md got real values back", "Henrik Dahl" in notes and "henrik.dahl@quillfield.com" in notes,
      "missing file" if not notes else "")

vault = glob.glob(f"{home}/sessions/*.json")
plain = [f for f in vault if any(v.lower() in open(f, errors="ignore").read().lower() for v in vals)]
check("vault is not plaintext", bool(vault) and not plain, f"{len(vault)} files, {len(plain)} with plaintext")

print("\nanswer:", result[:600])
sys.exit(0 if all(results) else 1)
EOF
