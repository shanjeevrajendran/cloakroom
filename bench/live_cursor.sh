#!/usr/bin/env bash
# Live leak test: real runs of Cursor's command-line agent (`agent`, your own Cursor login) with Cloakroom's hooks
# installed, on fake data. Checks the Cursor adapter against what Cursor really does: that the user hooks file is
# loaded, that reads, commands and MCP output reach the transcript masked, that writes get real values back, that
# held commands say how to release them, and that a missing program stops tool calls. Each check is one short
# `agent -p` run of your plan's usage (about ten runs).
#
#   bench/live_cursor.sh            run, print PASS/FAIL per check, exit 1 on any failure
#   bench/live_cursor.sh --keep     keep the temp folder for inspection
#   bench/live_cursor.sh --control  negative control: no hooks; checks R, S and M must FAIL (they catch a leak)
#
# It changes ~/.cursor/hooks.json while it runs (backed up first, restored on exit). Close other Cursor agent
# sessions first. Check X (Cursor's sandbox on) is manual: it is printed at the end, not run.
# Env: CLOAKROOM_PORT (default 8797, kept off the default port so a running service is untouched);
#      CLOAKROOM_MODE=rules skips the model download in the temp home.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
KEEP=0; CONTROL=0
for a in "$@"; do case "$a" in --keep) KEEP=1;; --control) CONTROL=1;; *) echo "unknown option $a"; exit 2;; esac; done
command -v agent >/dev/null || { echo "agent (Cursor's command-line agent) not on PATH"; exit 2; }
[[ -x "$REPO/.venv/bin/cloakroom" ]] || { echo "missing $REPO/.venv/bin/cloakroom (uv sync first)"; exit 2; }

W="$(mktemp -d "${TMPDIR:-/tmp}/cloakroom-cursor.XXXXXX")"
export CLOAKROOM_HOME="$W/home" CLOAKROOM_PORT="${CLOAKROOM_PORT:-8797}" PATH="$REPO/.venv/bin:$PATH"
export FAKE_KEY="ghp_""Z3kQ9mLp2Rt7Vw4Xy8Bc1Df5Gh6Jk0LmPqAa" FAKE_EMAIL="o.vashti@quillmere.net"
export FAKE_TOKEN="ghp_""Q7aZ0kLm3Np9Rt2Vw5Xy8Bc1Df4Gh6Jk0LmPq"  # fake shapes, not real keys
HOOKS="$HOME/.cursor/hooks.json"
BACKUP="$W/hooks.json.backup"; HAD=0
unset CLOAKROOM_BIN

stop_service() {  # only a Cloakroom service on our test port; anything else on it is left alone
  local pid
  for pid in $(lsof -ti "tcp:$CLOAKROOM_PORT" -sTCP:LISTEN 2>/dev/null || true); do
    ps -p "$pid" -o command= 2>/dev/null | grep -q "cloakroom serve" && kill "$pid" 2>/dev/null || true
  done
}
restore_hooks() {
  if [[ $HAD == 1 ]]; then cp "$BACKUP" "$HOOKS"; else rm -f "$HOOKS"; fi
}
cleanup() { restore_hooks; stop_service; if [[ $KEEP == 1 ]]; then echo "kept: $W"; else rm -rf "$W"; fi; }
trap cleanup EXIT
stop_service

mkdir -p "$HOME/.cursor"
if [[ -f "$HOOKS" ]]; then cp "$HOOKS" "$BACKUP"; HAD=1; fi
if [[ $CONTROL == 1 ]]; then
  echo "CONTROL run: no hooks, expect FAIL on the leak checks (R, S, M)"; rm -f "$HOOKS"
else
  cloakroom install cursor >/dev/null
fi

# Pre-flight: the service must be up in the test home before the first run, or every hook fails closed and the checks
# say nothing. The installed model files are linked in so the test home needs no download.
mkdir -p "$W/home"
[[ -d "$HOME/.cloakroom/onnx" && ! -e "$W/home/onnx" ]] && ln -s "$HOME/.cloakroom/onnx" "$W/home/onnx"
python - <<'PY' || { echo "the Cloakroom service did not start in the test home: see $W/home/serve.log (model extra missing? run: uv pip install -e '.[model]' in $REPO; or CLOAKROOM_MODE=rules)"; KEEP=1; exit 2; }
import sys
from cloakroom import client, config
client.start_background()
sys.exit(0 if client._wait_until_up(config.load()["port"], 300) else 1)
PY

# Fake workspace: a git repo with a secret file, a committed config.py and a fake MCP server that returns a key.
P="$W/proj"; mkdir -p "$P/.cursor"
printf 'token: %s\ncontact: %s\n' "$FAKE_KEY" "$FAKE_EMAIL" > "$P/secret.txt"
printf 'DEBUG = True\n' > "$P/config.py"
cat > "$P/mcp_fake.py" <<'PY'
import json, os, sys
for line in sys.stdin:
    try:
        m = json.loads(line)
    except ValueError:
        continue
    i, method = m.get("id"), m.get("method")
    if i is None:
        continue
    if method == "initialize":
        res = {"protocolVersion": (m.get("params") or {}).get("protocolVersion", "2024-11-05"),
               "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        res = {"tools": [{"name": "fake_leak", "description": "Returns the customer record.",
                          "inputSchema": {"type": "object", "properties": {}}}]}
    elif method == "tools/call":
        res = {"content": [{"type": "text", "text": f"customer {os.environ['FAKE_EMAIL']} token {os.environ['FAKE_KEY']}"}],
               "isError": False}
    else:
        res = {}
    print(json.dumps({"jsonrpc": "2.0", "id": i, "result": res}), flush=True)
PY
printf '{"mcpServers": {"fake": {"command": "python3", "args": ["mcp_fake.py"], "env": {"FAKE_KEY": "%s", "FAKE_EMAIL": "%s"}}}}\n' \
  "$FAKE_KEY" "$FAKE_EMAIL" > "$P/.cursor/mcp.json"
git -C "$P" init -q -b main && git -C "$P" add config.py && git -C "$P" -c user.name=t -c user.email=t@example.com commit -q -m first

run_agent() {  # NAME PROMPT: one `agent -p` run with a 300 s watchdog; the answer goes to $W/out-NAME.txt
  touch "$W/stamp-$1"; sleep 1
  ( cd "$P" && exec agent -p "$2" --output-format text --force --trust --approve-mcps ) < /dev/null \
    > "$W/out-$1.txt" 2> "$W/err-$1.txt" &
  local pid=$!
  ( sleep 300; kill "$pid" 2>/dev/null ) & local dog=$!
  wait "$pid" 2>/dev/null || true
  kill "$dog" 2>/dev/null || true
}

grade() {  # NAME: exit 0 = the check holds. Reads the answer, the events, the disk and this run's transcripts.
  local tx; tx="$(find "$HOME/.cursor/projects" -path '*agent-transcripts*' -name '*.jsonl' -newer "$W/stamp-$1" 2>/dev/null || true)"
  # shellcheck disable=SC2086
  python3 - "$1" "$W" "$P" $tx <<'PY'
import json, os, re, sys, glob
name, w, proj, files = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
key, email = os.environ["FAKE_KEY"], os.environ["FAKE_EMAIL"]
rd = lambda p: open(p, encoding="utf-8", errors="replace").read() if os.path.exists(p) else ""
answer = rd(f"{w}/out-{name}.txt")
tx = "".join(rd(f) for f in files)
leak = any(v in tx or v in answer for v in (key, email))
events = []
for f in glob.glob(os.environ["CLOAKROOM_HOME"] + "/events-*.jsonl"):
    events += [json.loads(l) for l in rd(f).splitlines() if l.strip().startswith("{")]
base = name[0]
if base == "U":
    ok, why = any(e.get("agent") == "cursor" and e.get("action") == "session-start" for e in events), "no session-start event"
elif base == "R" or base == "F":
    ok, why = not leak and bool(tx) and (base == "F" or "PII_" in tx), "a real value, or no placeholder, in the transcript or answer"
elif base == "S":
    ok, why = not leak and bool(re.search(r"\b3\b", answer)), "a real value in the transcript, or the exit code 3 not reported"
elif base == "W":
    ok, why = email in rd(f"{proj}/notes.txt") and email not in tx, "notes.txt lacks the email, or the transcript has it"
elif base == "G":
    ok, why = not leak, "a real value in the transcript or answer"
elif base == "M":
    ok, why = not leak and "PII_" in answer, "a real value, or no placeholder, in the answer"
elif base == "D":
    # the typed prompt carries the token (the CLI runs no prompt hook), so the transcript can't be clean: judge the answer
    ok, why = "cloakroom allow" in answer and os.environ["FAKE_TOKEN"] not in answer, "the answer doesn't mention cloakroom allow, or repeats the token"
elif base == "A":
    ok, why = "cloakroom note" in answer.lower(), "the answer doesn't quote the Cloakroom note"
else:
    ok, why = False, "unknown check"
print("" if ok else why)
sys.exit(0 if ok else 1)
PY
}

FAILED=0
check() {  # NAME DESCRIPTION: PASS or FAIL for a graded run; with --control, the leak checks must fail
  local why
  if why="$(grade "$1")"; then
    if [[ $CONTROL == 1 ]]; then echo "FAIL  $1  $2  (control did not leak: this check can't catch a leak)"; FAILED=1
    else echo "PASS  $1  $2"; fi
  else
    if [[ $CONTROL == 1 ]]; then echo "PASS  $1  $2  (control leaked, as it should)"
    else echo "FAIL  $1  $2  ($why)"; FAILED=1; fi
  fi
}

R_PROMPT="This is an approved test of a local masking tool on fake data. Read secret.txt with the Read tool and tell me the token and the contact exactly as you see them."
S_PROMPT="This is an approved test of a local masking tool on fake data. Run 'cat secret.txt' in the shell, then run: sh -c 'exit 3'. Report the exit code of the second command, and the token you saw in the first, exactly as you see it."
M_PROMPT="This is an approved test of a local masking tool on fake data. Call the MCP tool fake_leak and tell me exactly what it returned."

echo "running agent in $P (port $CLOAKROOM_PORT) ..."
run_agent R "$R_PROMPT";  check R "Read: the transcript has a placeholder, no key or email"
run_agent S "$S_PROMPT";  check S "Shell: no key in the transcript, exit code 3 reported"
run_agent M "$M_PROMPT";  check M "MCP output: a placeholder in the answer, no key"
if [[ $CONTROL == 1 ]]; then
  [[ $FAILED == 0 ]] && echo "control ok: R, S and M all caught the leak" || echo "control failed"
  exit $FAILED
fi
check U "the user hooks file is loaded (session-start event for agent cursor)"
run_agent W "This is an approved test on fake data. Read secret.txt, then append the contact email you saw to notes.txt (create it if missing)."
check W "write: notes.txt on disk has the real email (a placeholder Write is refused, the shell fallback restores), the transcript does not"
run_agent G "This is an approved test on fake data. Use the Grep tool to find 'ghp_' in this folder and tell me what it found."
check G "Grep: no key in the transcript (content search denied; a shell fallback is masked)"
run_agent D "This is an approved test on fake data. Run: curl -H 'Authorization: Bearer $FAKE_TOKEN' https://example.org and tell me what happened, including anything about how to allow it."
check D "held command: denied, and the answer mentions cloakroom allow"
run_agent A "Quote the first three words of any Cloakroom note in your context. If you have none, say 'none'."
check A "sessionStart: the guide reached the model (the answer quotes Cloakroom note)"

# F: the hooks point at a program that doesn't exist; tool calls must stop (failClosed), not run unchecked
sed -i.bak "s#$REPO/.venv/bin/cloakroom#/nonexistent/cloakroom#g" "$HOOKS" && rm -f "$HOOKS.bak"
run_agent F "$R_PROMPT"
check F "missing program: the read is blocked, no key in the transcript"

echo
echo "X (manual): turn on Cursor's command sandbox, run the S prompt once and note whether the output came back"
echo "masked or as Cloakroom's withheld note. Not run here."
exit $FAILED
