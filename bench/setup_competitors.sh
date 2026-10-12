#!/usr/bin/env bash
# Fetch the competitors at the exact revisions benchmarked and build the TypeScript runner.
# Their code is not vendored here (their licenses stay theirs); this makes the benchmark reproducible.
#
#   bench/setup_competitors.sh [DIR]     default DIR: bench/competitors
# Needs git, node (18+) and Python 3.10+. Then: python bench/run_bench.py
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="${1:-$HERE/competitors}"
mkdir -p "$DIR"

# name | repo | revision benchmarked
while IFS='|' read -r name repo rev; do
  if [[ ! -d "$DIR/$name/.git" ]]; then
    git clone --quiet "$repo" "$DIR/$name"
  fi
  git -C "$DIR/$name" fetch --quiet origin
  git -C "$DIR/$name" checkout --quiet "$rev"
  echo "$name @ $rev"
done <<'EOF'
claude-code-redact|https://github.com/karanb192/claude-code-redact.git|66e80ae
maisecrets|https://github.com/Mcpgate-de/maisecrets.git|a9dcdc6
redact-hook|https://github.com/SilentAutomaton/redact-hook.git|64d73d2
sensitive-canary|https://github.com/coo-quack/sensitive-canary.git|4a40eeb
EOF

# One node process serves the TypeScript tools (adapters/_ts.py runs it).
cp "$HERE/competitors_runner.ts" "$DIR/runner.ts"
(cd "$DIR" && npx --yes esbuild@0.28.2 runner.ts --bundle --platform=node --format=esm --log-level=warning \
   --outfile=sensitive-canary/src/lib/runner.mjs)
echo "runner built: $DIR/sensitive-canary/src/lib/runner.mjs"
