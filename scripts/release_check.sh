#!/usr/bin/env bash
# One-shot pre-publish gate (release checklist step 2). Stops at the first failing step.
#   scripts/release_check.sh           every step
#   scripts/release_check.sh --quick   skip the live test and the bare-venv suite
set -uo pipefail
cd "$(dirname "$0")/.."
QUICK=0
for a in "$@"; do case "$a" in --quick) QUICK=1;; *) echo "unknown option $a"; exit 2;; esac; done

step() {  # step <name> <command...>
  local name=$1; shift
  local log; log=$(mktemp "${TMPDIR:-/tmp}/release-check.XXXXXX")
  local t0=$SECONDS
  if "$@" >"$log" 2>&1; then
    echo "ok $name ($((SECONDS - t0))s)"; rm -f "$log"
  else
    echo "FAIL $name"; tail -n 30 "$log"; echo "(full output: $log)"; exit 1
  fi
}

version_init() {
  local want got
  want=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)
  got=$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' src/cloakroom/__init__.py | head -1)
  [[ -n $want && $want == "$got" ]] || { echo "pyproject $want vs __init__ $got"; return 1; }
}
bare_venv() {
  local v=${TMPDIR:-/tmp}/cloakroom-bare
  rm -rf "$v" && python3 -m venv "$v" && "$v/bin/pip" -q install -e . pytest && "$v/bin/pytest" -q tests
}

step version-init version_init
step version-pins env PYTHONPATH=src python3 -m pytest -q tests/test_shell_and_hooks.py::test_plugin_installers_pin_this_release
step completeness python3 scripts/release_complete.py
step hooks-current python3 scripts/gen_hooks.py --check
step polyglot python3 scripts/check_polyglot.py
step tests env PYTHONPATH=src python3 -m pytest -q tests
if [[ $QUICK == 0 ]]; then
  step live-test bench/live_test.sh
  step bare-venv bare_venv
fi
echo "release check passed"
