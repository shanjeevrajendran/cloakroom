#!/usr/bin/env bash
# Records one real Claude Code run with the Cloakroom plugin on fake data, for the README demo.
# Writes demo/run/ (fixtures, transcript, files on disk); demo/render.py turns it into assets/demo.gif.
# Uses one short `claude -p` run of your plan's usage.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"; OUT="$REPO/demo/run"
command -v claude >/dev/null || { echo "claude CLI not on PATH"; exit 2; }
[[ -x "$REPO/.venv/bin/cloakroom" ]] || { echo "missing $REPO/.venv/bin/cloakroom (uv sync first)"; exit 2; }
rm -rf "$OUT"; mkdir -p "$OUT/proj/support" "$OUT/home"
export CLOAKROOM_HOME="$OUT/home" CLOAKROOM_PORT="${CLOAKROOM_PORT:-8797}" PATH="$REPO/.venv/bin:$PATH"
stop() { local p; p="$(lsof -ti "tcp:$CLOAKROOM_PORT" -sTCP:LISTEN 2>/dev/null || true)"; [[ -n "$p" ]] && kill $p 2>/dev/null || true; }
trap stop EXIT; stop

# Fake data only.
printf 'STRIPE_SECRET_KEY=sk_live_51Hx9QaTmV7tR4pW8nK3jH6bYd2Lc\nAPP_ENV=production\n' > "$OUT/proj/.env"
cat > "$OUT/proj/support/ticket-4412.txt" <<'T'
Ticket 4412 (billing)
Nadia Petrov called about a double charge on her March invoice.
She asked us to email her at npetrov@marrowgate.io or call (415) 555-2381.
Shipping address on file: 14 Alder Lane, Portland, OR 97214.
T
cp -R "$OUT/proj" "$OUT/original"

PROMPT="Read support/ticket-4412.txt and .env. Write reply.md: a two-line reply to the customer, addressed by name, confirming we will refund the double charge to the card on file. Then tell me in one short line which Stripe key the app uses."
echo "$PROMPT" > "$OUT/prompt.txt"
(cd "$OUT/proj" && claude -p "$PROMPT" --plugin-dir "$REPO/plugin" --allowedTools "Read,Write" \
   --output-format json < /dev/null > "$OUT/run.json" 2> "$OUT/run.err")
SID=$(python3 -c "import json;print(json.load(open('$OUT/run.json'))['session_id'])")
cp ~/.claude/projects/*/"$SID".jsonl "$OUT/transcript.jsonl"
echo "recorded: $OUT"
