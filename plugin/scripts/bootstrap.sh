#!/usr/bin/env bash
# First-start installer, run in the background by the plugin's hooks when the cloakroom program is missing.
# Stage 1: install `cloakroom` (small, seconds) -> rules-only protection.
# Stage 2: the model extras (about 1 GB on a Mac, up to about 4 GB elsewhere; minutes) plus local OCR for scans and
#          photos (the ocr extra, about 100 MB; if it fails to install, stage 2 is retried without it); on a machine
#          without an Apple Silicon or NVIDIA GPU it also builds the fast CPU model (cloakroom export-onnx --if-no-gpu)
#          before full mode switches on by itself.
# Installer, the first that exists: uv, pipx, else a private virtualenv in ~/.cloakroom/venv (needs Python 3.10+).
# Only Python packages are downloaded; uv itself is never fetched for you.
# One installer at a time (lock dir; a lock older than 30 min is stale). Progress: ~/.cloakroom/bootstrap.log.
# bootstrap.sh --check only says whether an installer exists (exit 0) and installs nothing.
# CLOAKROOM_SPEC_RULES / CLOAKROOM_SPEC_FULL / CLOAKROOM_SPEC_NO_OCR override what is installed, CLOAKROOM_INSTALLER picks one (tests).
set -u
# --check: exit 0 when this machine has something to install with (the hook asks this before starting the install)
if [ "${1:-}" = --check ]; then
  command -v uv >/dev/null 2>&1 || command -v pipx >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1
  exit
fi
H="${CLOAKROOM_HOME:-$HOME/.cloakroom}"
mkdir -p "$H" && chmod 700 "$H"
LOCK="$H/bootstrap.lock" STATE="$H/bootstrap.state" LOG="$H/bootstrap.log"
if ! mkdir "$LOCK" 2>/dev/null; then
  [ -n "$(find "$LOCK" -maxdepth 0 -mmin +30 2>/dev/null)" ] || exit 0
  rm -rf "$LOCK" && mkdir "$LOCK" || exit 0
fi
trap 'rm -rf "$LOCK"' EXIT
RULES="${CLOAKROOM_SPEC_RULES:-cloakroom==0.4.1}"  # pinned: each plugin version installs its own release
FULL="${CLOAKROOM_SPEC_FULL:-cloakroom[model,torch,ocr]==0.4.1}"
NO_OCR="${CLOAKROOM_SPEC_NO_OCR:-cloakroom[model,torch]==0.4.1}"  # stage 2 again without OCR if its wheels fail

py310() {  # a Python 3.10+ interpreter on PATH, or nothing
  for p in python3 python; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      echo "$p"; return
    fi
  done
}
WHO="${CLOAKROOM_INSTALLER:-}"
if [ -z "$WHO" ]; then
  if command -v uv >/dev/null 2>&1; then WHO=uv
  elif command -v pipx >/dev/null 2>&1; then WHO=pipx
  elif [ -n "$(py310)" ] && "$(py310)" -c 'import venv, ensurepip' 2>/dev/null; then WHO=venv
  elif [ -n "$(py310)" ]; then WHO=novenv  # Debian/Ubuntu ship python3 without venv (package python3-venv)
  else WHO=none; fi
fi

install() {  # install SPEC STAGE(0 rules | 1 model and OCR | 2 model only)
  case "$WHO" in
    # uv and pipx get the release spelled out (the Claude plugin directory checks these pins); the CLOAKROOM_SPEC_*
    # overrides, for tests, apply to the virtualenv route
    uv)   case "$2" in
            1) uv tool install --force "cloakroom[model,torch,ocr]==0.4.1" ;;
            2) uv tool install --force "cloakroom[model,torch]==0.4.1" ;;
            *) uv tool install "cloakroom==0.4.1" ;;
          esac ;;
    pipx) case "$2" in
            1) pipx install --force "cloakroom[model,torch,ocr]==0.4.1" ;;
            2) pipx install --force "cloakroom[model,torch]==0.4.1" ;;
            *) pipx install "cloakroom==0.4.1" ;;
          esac ;;
    venv) { [ -x "$H/venv/bin/python" ] || "$(py310)" -m venv "$H/venv"; } && PIP_DISABLE_PIP_VERSION_CHECK=1 "$H/venv/bin/python" -m pip install -q -U "$1" ;;
    *)    return 1 ;;
  esac
}

cloakroom_bin() {  # where the installer put the program (often not on the PATH Claude Code sees)
  UVB=""; [ "$WHO" = uv ] && UVB="$(uv tool dir --bin 2>/dev/null)/cloakroom"
  for c in "$(command -v cloakroom 2>/dev/null)" "$UVB" "$HOME/.local/bin/cloakroom" "$H/venv/bin/cloakroom"; do
    if [ -n "$c" ] && [ -x "$c" ]; then echo "$c"; return; fi
  done
}

{
  echo "$(date '+%F %T') installer: $WHO"
  if [ "$WHO" = novenv ]; then
    echo failed > "$STATE"
    echo "Python is here but its venv module isn't (Ubuntu/Debian: sudo apt install python3-venv), and there is no uv or pipx: install one of them (uv: https://docs.astral.sh/uv/), then restart Claude Code"
    exit 1
  fi
  if [ "$WHO" = none ]; then
    echo failed > "$STATE"
    echo "no installer found: install uv (https://docs.astral.sh/uv/) or Python 3.10+, then restart Claude Code"
    exit 1
  fi
  echo "$(date '+%F %T') stage 1 of 2: installing $RULES (rules-only protection)"
  echo rules > "$STATE"
  if ! install "$RULES" 0; then echo failed > "$STATE"; echo "stage 1 failed"; exit 1; fi
  echo model > "$STATE"
  echo "$(date '+%F %T') stage 2 of 2: installing the model ($FULL; about 1 GB on a Mac, up to about 4 GB elsewhere, of which local OCR about 100 MB)"
  OK=0
  if install "$FULL" 1; then OK=1
  else
    echo "$(date '+%F %T') the install with OCR failed: retrying without it ($NO_OCR); scanned PDFs and photos stay held back"
    install "$NO_OCR" 2 && OK=1
  fi
  if [ "$OK" = 1 ]; then
    CR="$(cloakroom_bin)"
    if [ -n "$CR" ]; then
      echo "$(date '+%F %T') building the fast CPU model if this machine has no GPU (a few minutes)"
      "$CR" export-onnx --if-no-gpu || echo "CPU model build failed: full mode still works, just slower"
    fi
    rm -f "$STATE"; echo "$(date '+%F %T') done: full protection"
    # start the service now, so the user's next prompt doesn't wait for the model's first load
    if [ -n "$CR" ]; then (nohup "$CR" serve </dev/null >>"$H/serve.log" 2>&1 &); echo "$(date '+%F %T') service starting"; fi
  else
    echo model-failed > "$STATE"; echo "stage 2 failed: staying on rules-only protection"
  fi
} >>"$LOG" 2>&1
