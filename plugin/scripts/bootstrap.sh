#!/usr/bin/env bash
# First-start installer, run in the background by the plugin's hooks when the cloakroom program is missing.
# Stage 1: install `cloakroom` (small, seconds) -> rules-only protection.
# Stage 2: the model extras (about 1 GB on a Mac, up to about 4 GB elsewhere; minutes) plus local OCR for scans and
#          photos (the ocr extra, about 100 MB; if it fails to install, stage 2 is retried without it); on a machine
#          without an Apple Silicon or NVIDIA GPU it also builds the fast CPU model (cloakroom export-onnx --if-no-gpu)
#          before full mode switches on by itself. Intel Macs stop after stage 1: PyTorch and ONNX Runtime publish no
#          Intel Mac builds, so they get rules-only protection.
# Installer, the first that works: uv, pipx, else uv fetched for you (Astral's installer for the pinned uv version,
# run only when its SHA-256 matches; uv then brings its own Python 3.12), else a private virtualenv in
# ~/.cloakroom/venv (needs Python 3.10+ with venv).
# Afterwards ~/.local/bin goes on the PATH of new terminals: uv tool update-shell, pipx ensurepath, or (virtualenv)
# the same profile line uv writes.
# One installer at a time (lock dir; a lock older than 30 min is stale). Progress: ~/.cloakroom/bootstrap.log.
# On failure ~/.cloakroom/bootstrap.reason says why (nofetch | offline | checksum | failed) for the hook's message.
# bootstrap.sh --check only says whether this machine has something to install with (exit 0) and installs nothing.
# bootstrap.sh --upgrade: the plugin was updated (the hook saw ~/.cloakroom/upgrade.stamp differ from its version).
#   It asks the installed program's own Python (upgrade_probe.py) for its version, extras and environment, and, when
#   it is older than PIN and was installed by uv, pipx or this script's virtualenv, installs PIN on the same route with
#   the same extras, then restarts a running `cloakroom serve`. Newer, dev and editable installs are left alone. An
#   older install on any other route (Homebrew, system pip, conda) gets the steps once, at the next session start
#   (~/.cloakroom/upgrade.notice). The old program serves until the installer swaps it; a hook that finds no program
#   in that moment denies the tool call. A failed upgrade keeps the old version and retries after 10 minutes.
#   While files are swapped, $LOCK/upgrade exists and the hooks hold tool calls without starting the program. A
#   program that doesn't run (missing module, lost Python) is removed and installed again from scratch (stage 1,
#   stage 2); it is stamped only once `cloakroom --version` works, and a failed reinstall ends as state failed.
# Tests: CLOAKROOM_SPEC_RULES / CLOAKROOM_SPEC_FULL / CLOAKROOM_SPEC_NO_OCR override what is installed on every route
# (e.g. "cloakroom @ file:///path/to.whl"), CLOAKROOM_INSTALLER picks one, CLOAKROOM_PLATFORM fakes `uname -s`-`uname -m`,
# CLOAKROOM_SPEC_UPGRADE replaces what --upgrade installs.
set -u
PIN=0.6.6  # the release this plugin version installs (scripts/gen_hooks.py bakes the same into the hooks)
UV_VERSION=0.13.1
UV_SH_URL="https://astral.sh/uv/$UV_VERSION/install.sh"
# SHA-256 of that script: the digest GitHub lists for uv-installer.sh in the uv 0.13.1 release
UV_SH_SHA256=7c482d409673a53f4d9bd34ae95c9166be3b8d89ca82f27dd7bca3cab457afff
H="${CLOAKROOM_HOME:-$HOME/.cloakroom}"
STAMP="$H/upgrade.stamp" NOTICE="$H/upgrade.notice" PROBE="$(dirname "$0")/upgrade_probe.py"
LOCK="$H/bootstrap.lock" STATE="$H/bootstrap.state" LOG="$H/bootstrap.log" REASONF="$H/bootstrap.reason"
PLAT="${CLOAKROOM_PLATFORM:-$(uname -s)-$(uname -m)}"

py310() {  # a Python 3.10+ interpreter on PATH, or nothing
  for p in python3 python; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      echo "$p"; return
    fi
  done
}
downloader() {  # curl, else wget, or nothing
  if command -v curl >/dev/null 2>&1; then echo curl; elif command -v wget >/dev/null 2>&1; then echo wget; fi
}
find_uv() {  # uv on PATH, or where Astral's installer puts it (the PATH Claude Code sees is often stale)
  for c in "$(command -v uv 2>/dev/null)" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -n "$c" ] && [ -x "$c" ]; then echo "$c"; return; fi
  done
}

# Numbered steps for the user, per failure reason (the hook shows the same steps: scripts/gen_hooks.py)
steps() {
  case "$1" in
    nofetch) if [ "${PLAT%%-*}" = Darwin ]; then
               echo "To finish: 1. Install uv: brew install uv (or see https://docs.astral.sh/uv/getting-started/installation/). 2. Then restart Claude Code (/exit, then claude). Log: $LOG"
             else
               echo "To finish: 1. Install curl: sudo apt install curl (Ubuntu/Debian) or sudo dnf install curl (Fedora/RHEL). 2. Then restart Claude Code (/exit, then claude). Log: $LOG"
             fi ;;
    offline) echo "To finish: 1. Connect to the internet (behind a proxy or firewall, allow astral.sh, releases.astral.sh, pypi.org, files.pythonhosted.org and huggingface.co). 2. Then restart Claude Code (/exit, then claude); it also retries by itself in 10 minutes. Log: $LOG" ;;
    checksum) echo "To finish: 1. Install uv yourself: brew install uv on a Mac, or see https://docs.astral.sh/uv/getting-started/installation/ (Ubuntu/Debian alternative: sudo apt install python3-venv). 2. Then restart Claude Code (/exit, then claude). Log: $LOG" ;;
    model) echo "To finish the model install: 1. Check the internet connection and free disk space (about 1 GB on a Mac, up to about 4 GB elsewhere). 2. Run: $(retry_cmd) 3. Delete $STATE. 4. Then restart Claude Code (/exit, then claude). Rules-only protection stays on until then. Log: $LOG" ;;
    *) echo "To finish: 1. Read the error above in this log. 2. Fix it, or install Cloakroom yourself (command: https://github.com/shanjeevrajendran/cloakroom#get-started). 3. Then restart Claude Code (/exit, then claude). Log: $LOG" ;;
  esac
}

# --check: exit 0 when this machine has something to install with (the hook asks this before starting the install)
if [ "${1:-}" = --check ]; then
  if [ -n "$(find_uv)" ] || command -v pipx >/dev/null 2>&1 || [ -n "$(downloader)" ] || [ -n "$(py310)" ]; then exit 0; fi
  # nothing at all: leave the steps in the log once (the hook asks again on every call)
  MSG="nothing to install Cloakroom with (no curl, wget, uv, pipx or Python 3.10+). $(steps nofetch)"
  mkdir -p "$H" 2>/dev/null && chmod 700 "$H" 2>/dev/null
  [ "$(tail -n 1 "$LOG" 2>/dev/null)" = "$MSG" ] || echo "$MSG" >>"$LOG" 2>/dev/null
  exit 1
fi
mkdir -p "$H" && chmod 700 "$H"
if ! mkdir "$LOCK" 2>/dev/null; then
  [ -n "$(find "$LOCK" -maxdepth 0 -mmin +30 2>/dev/null)" ] || exit 0
  rm -rf "$LOCK" && mkdir "$LOCK" || exit 0
fi
trap 'rm -rf "$LOCK"' EXIT
RULES="${CLOAKROOM_SPEC_RULES:-cloakroom==0.6.6}"  # pinned: each plugin version installs its own release
FULL="${CLOAKROOM_SPEC_FULL:-cloakroom[model,torch,ocr]==0.6.6}"
NO_OCR="${CLOAKROOM_SPEC_NO_OCR:-cloakroom[model,torch]==0.6.6}"  # stage 2 again without OCR if its wheels fail
TESTSPEC="${CLOAKROOM_SPEC_RULES:-}${CLOAKROOM_SPEC_FULL:-}${CLOAKROOM_SPEC_NO_OCR:-}"
# Intel Mac: newer cryptography releases ship no Intel Mac wheel and would need a Rust build, so only prebuilt wheels
# are used there (the resolver then picks the last release that has one); the model's runtimes have none at all
NB=""; INTEL_MAC=0
if [ "$PLAT" = Darwin-x86_64 ]; then INTEL_MAC=1 NB=--no-build; export PIP_ONLY_BINARY=:all:; fi
UV="" REASON=""

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1"
  elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"
  elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 -r "$1"
  fi | { read -r h _ && echo "$h"; }
}
online() {  # can we reach PyPI? (only asked after a failed install, to tell "offline" from other errors)
  case "$(downloader)" in
    curl) curl -fsS --max-time 15 -o /dev/null https://pypi.org/simple/cloakroom/ 2>/dev/null ;;
    wget) wget -q --timeout=15 --tries=1 -O /dev/null https://pypi.org/simple/cloakroom/ 2>/dev/null ;;
    *) return 0 ;;
  esac
}
fetch_uv() {  # Astral's installer for the pinned uv, run only if its SHA-256 matches; sets UV, or REASON on failure
  local d tmp rc got
  d="$(downloader)"
  if [ -z "$d" ]; then REASON=nofetch; echo "no uv or pipx, and neither curl nor wget to fetch uv with"; return 1; fi
  echo "$(date '+%F %T') no uv or pipx: fetching uv $UV_VERSION ($UV_SH_URL, with $d)"
  tmp="$(mktemp "${TMPDIR:-/tmp}/cloakroom-uv-install.XXXXXX")" || { REASON=failed; return 1; }
  if [ "$d" = curl ]; then curl --proto '=https' --tlsv1.2 -fsSL --retry 2 --connect-timeout 20 --max-time 300 -o "$tmp" "$UV_SH_URL"; rc=$?
  else wget -q --https-only --timeout=20 --tries=3 -O "$tmp" "$UV_SH_URL"; rc=$?; fi
  if [ "$rc" -ne 0 ]; then
    case "$d:$rc" in curl:5|curl:6|curl:7|curl:28|curl:35|curl:56|wget:4) REASON=offline ;; *) REASON=failed ;; esac
    echo "could not download the uv installer ($d exit $rc)"; rm -f "$tmp"; return 1
  fi
  got="$(sha256_of "$tmp")"
  if [ "$got" != "$UV_SH_SHA256" ]; then
    REASON=checksum; echo "uv installer checksum mismatch (got ${got:-nothing}, want $UV_SH_SHA256): not running it"
    rm -f "$tmp"; return 1
  fi
  # into ~/.local/bin, without touching shell profiles: put_on_path does that, and logs it
  UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh "$tmp"; rc=$?
  rm -f "$tmp"
  if [ "$rc" -eq 0 ] && [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"; echo "uv $UV_VERSION installed: $UV"; return 0; fi
  if online; then REASON=failed; else REASON=offline; fi
  echo "the uv installer failed (exit $rc)"; return 1
}

install() {  # install SPEC STAGE(0 rules | 1 model and OCR | 2 model only)
  case "$WHO" in
    # uv and pipx get the release spelled out (the Claude plugin directory checks these pins); the CLOAKROOM_SPEC_*
    # overrides, for tests, replace them on every route. $NB is --no-build on an Intel Mac, else empty (unquoted).
    uv)   if [ -n "$TESTSPEC" ]; then
            if [ "$2" = 0 ]; then "$UV" tool install --python 3.12 $NB "$1"; else "$UV" tool install --python 3.12 $NB --force "$1"; fi
          else case "$2" in
            1) "$UV" tool install --python 3.12 $NB --force "cloakroom[model,torch,ocr]==0.6.6" ;;
            2) "$UV" tool install --python 3.12 $NB --force "cloakroom[model,torch]==0.6.6" ;;
            *) "$UV" tool install --python 3.12 $NB "cloakroom==0.6.6" ;;
          esac; fi ;;
    pipx) if [ -n "$TESTSPEC" ]; then
            if [ "$2" = 0 ]; then pipx install "$1"; else pipx install --force "$1"; fi
          else case "$2" in
            1) pipx install --force "cloakroom[model,torch,ocr]==0.6.6" ;;
            2) pipx install --force "cloakroom[model,torch]==0.6.6" ;;
            *) pipx install "cloakroom==0.6.6" ;;
          esac; fi ;;
    venv) { [ -x "$H/venv/bin/python" ] || "$(py310)" -m venv "$H/venv"; } && PIP_DISABLE_PIP_VERSION_CHECK=1 "$H/venv/bin/python" -m pip install -q -U "$1" ;;
    *)    return 1 ;;
  esac
}
retry_cmd() {  # the stage 2 command, for the user to run by hand
  case "$WHO" in
    uv) echo "$UV tool install --python 3.12 ${NB:+$NB }--force \"$FULL\"" ;;
    pipx) echo "pipx install --force \"$FULL\"" ;;
    *) echo "$H/venv/bin/python -m pip install -U \"$FULL\"" ;;
  esac
}

cloakroom_bin() {  # where the installer put the program (often not on the PATH Claude Code sees)
  UVB=""; [ "$WHO" = uv ] && UVB="$("$UV" tool dir --bin 2>/dev/null)/cloakroom"
  for c in "$(command -v cloakroom 2>/dev/null)" "$UVB" "$HOME/.local/bin/cloakroom" "$H/venv/bin/cloakroom"; do
    if [ -n "$c" ] && [ -f "$c" ] && [ -x "$c" ]; then echo "$c"; return; fi
  done
}

add_path_line() {  # add_path_line FILE: the line uv tool update-shell writes, unless FILE already names ~/.local/bin
  if grep -qsF "$HOME/.local/bin" "$1"; then echo "PATH: $1 already has $HOME/.local/bin"; return; fi
  printf '\n# cloakroom (the same line uv tool update-shell writes)\nexport PATH="%s:$PATH"\n' "$HOME/.local/bin" >>"$1" \
    && echo "PATH: added $HOME/.local/bin to $1"
}
put_on_path() {  # new terminals find ~/.local/bin: zsh reads ~/.zshenv, bash ~/.bashrc and its login profile
  local sh="${SHELL:-}"
  if [ -z "$sh" ]; then if [ "${PLAT%%-*}" = Darwin ]; then sh=/bin/zsh; else sh=/bin/bash; fi; fi
  case "$WHO" in
    uv)   echo "PATH: $UV tool update-shell"; SHELL="$sh" "$UV" tool update-shell && return ;;
    pipx) echo "PATH: pipx ensurepath"; pipx ensurepath && return ;;
  esac
  case "${sh##*/}" in  # the files uv picks: an existing bash login profile (else ~/.bash_profile), plus ~/.bashrc
    zsh)  add_path_line "${ZDOTDIR:-$HOME}/.zshenv" ;;
    bash) local f found=0
          for f in .bash_profile .bash_login .profile; do
            if [ -f "$HOME/$f" ]; then add_path_line "$HOME/$f"; found=1; fi
          done
          [ "$found" = 1 ] || add_path_line "$HOME/.bash_profile"
          add_path_line "$HOME/.bashrc" ;;
    *)    add_path_line "$HOME/.profile" ;;
  esac
}
publish() {  # venv route: link ~/.local/bin/cloakroom; every route: ~/.local/bin on the PATH of new terminals
  if [ -n "$CR" ] && [ "$CR" = "$H/venv/bin/cloakroom" ]; then
    mkdir -p "$HOME/.local/bin" && ln -sf "$CR" "$HOME/.local/bin/cloakroom" && CR="$HOME/.local/bin/cloakroom"
  fi
  put_on_path || echo "PATH: could not update your shell profile; add $HOME/.local/bin to PATH yourself, or run $CR"
}
fail() {  # fail REASON WHAT: state failed, the reason for the hook, the steps for the user
  echo failed > "$STATE"; echo "$1" > "$REASONF"
  echo "$(date '+%F %T') $2"; steps "$1"; exit 1
}

env_python() {  # the Python of the environment program $1 lives in, from its script's #! line, or nothing
  local f="$1" l n=0 l1="" l2="" p=""
  while [ -L "$f" ] && [ "$n" -lt 20 ]; do
    l="$(readlink "$f")"; case "$l" in /*) f="$l" ;; *) f="$(dirname "$f")/$l" ;; esac; n=$((n + 1))
  done
  { IFS= read -r l1; IFS= read -r l2; } <"$f" 2>/dev/null
  case "$l1" in
    # a long or spaced path: pip and uv write #!/bin/sh, then '''exec' "/path/python" "$0" "$@"
    '#!/bin/sh') p="${l2#\'\'\'exec\' }"; p="${p%% \"\$0\"*}"; p="${p#[\"\']}"; p="${p%[\"\']}" ;;
    '#!'*) p="${l1#??}"; p="${p%% *}" ;;
  esac
  if [ -n "$p" ] && [ -x "$p" ]; then echo "$p"; fi
}
canon() { (cd -P "$1" 2>/dev/null && pwd); }  # a folder with symlinks resolved, or nothing
json_text() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
stop_service() {  # stop_service PREFIX CANONICAL-PREFIX PROGRAM: stop this environment's `serve`, print how many
  local pid args n=0
  while read -r pid args; do
    case "$args" in
      *"$1/"*" serve"|*"$2/"*" serve"|*"$3 serve") kill "$pid" 2>/dev/null && n=$((n + 1)) ;;
    esac
  done <<EOF
$(ps -eo pid=,args= 2>/dev/null)
EOF
  echo "$n"
}
upgrade_install() {  # upgrade_install SPEC: the pinned release on the route the program came from, extras kept
  case "$WHO" in
    uv)   "$UV" tool install --python 3.12 $NB --force "$1" ;;
    pipx) pipx install --force "$1" ;;
    venv) PIP_DISABLE_PIP_VERSION_CHECK=1 "$H/venv/bin/python" -m pip install -q -U "$1" ;;
    *)    return 1 ;;
  esac
}
remove_install() {  # remove_install [PREFIX]: take away a broken program of ours, so the hook fails closed
  case "$WHO" in
    uv)   "$UV" tool uninstall cloakroom || { [ -n "${1:-}" ] && rm -rf "$1"; } ;;
    pipx) pipx uninstall cloakroom || { [ -n "${1:-}" ] && rm -rf "$1"; } ;;
    venv) rm -rf "$H/venv" ;;
  esac
  # a link left pointing at nothing (uv on macOS/Linux, and this script's venv route, link ~/.local/bin/cloakroom)
  if [ -L "$HOME/.local/bin/cloakroom" ] && [ ! -e "$HOME/.local/bin/cloakroom" ]; then rm -f "$HOME/.local/bin/cloakroom"; fi
}
# While the files are swapped, hooks answer for the program (tool calls held), so nothing runs half-replaced code
hold() { : >"$LOCK/upgrade"; }
unhold() { rm -f "$LOCK/upgrade"; }
upgrade() {  # the plugin moved to PIN: bring an older install up to it (see the header). 2 = install from scratch
  local CR PY="" KIND="" VER="" EXT="" PREFIX="" P UVDIR="" PIPXDIR="" SPEC CMD MSG NEW n i
  WHO=""; UV="$(find_uv)"
  CR="$(cloakroom_bin)"
  [ -n "$CR" ] || return 0  # nothing installed: the hook starts the first install instead
  PY="$(env_python "$CR")"
  if [ -n "$PY" ] && [ -f "$PROBE" ]; then
    read -r KIND VER EXT PREFIX <<EOF
$("$PY" -I "$PROBE" "$PIN" 2>/dev/null)
EOF
  fi
  # a program that doesn't run (its environment lost cloakroom, or its Python) is broken, never "done"
  [ -n "$PREFIX" ] && [ "$PREFIX" != - ] || PREFIX="${PY:+$(dirname "$(dirname "$PY")")}"
  if [ -z "$KIND" ] || [ "$KIND" = missing ] || ! "$CR" --version >/dev/null 2>&1; then KIND=broken; fi
  case "$KIND" in
    same|newer|dev) echo "$PIN" >"$STAMP"
      echo "$(date '+%F %T') upgrade check: cloakroom $VER ($KIND) left as is for plugin $PIN"; return 0 ;;
  esac
  [ "$EXT" = - ] && EXT=""
  SPEC="${CLOAKROOM_SPEC_UPGRADE:-cloakroom${EXT:+[$EXT]}==0.6.6}"
  P="$(canon "$PREFIX")"
  [ -n "$UV" ] && UVDIR="$(canon "$("$UV" tool dir 2>/dev/null)")"
  command -v pipx >/dev/null 2>&1 && PIPXDIR="$(canon "$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null)")"
  if [ -n "$P" ] && [ -n "$UVDIR" ] && [ "$P" = "$UVDIR/cloakroom" ]; then WHO=uv
  elif [ -n "$P" ] && [ -n "$PIPXDIR" ] && [ "$P" = "$PIPXDIR/cloakroom" ]; then WHO=pipx
  elif [ -n "$P" ] && [ "$P" = "$(canon "$H/venv")" ]; then WHO=venv
  fi
  if [ "$KIND" = broken ]; then
    if [ -z "$WHO" ]; then  # not ours to remove: the user reinstalls it, with steps at the next session start
      echo "failed:$PIN" >"$STAMP"
      MSG="cloakroom: the installed program ($CR) does not run, and it wasn't installed by uv, pipx or the plugin, so it can't repair itself. To finish: 1. Run: uv tool install --force --python 3.12 \"cloakroom==0.6.6\" (or reinstall it the way you installed it). 2. Then restart Claude Code (/exit, then claude)."
      printf '{"systemMessage": "%s"}\n' "$(json_text "$MSG")" >"$NOTICE"
      echo "$(date '+%F %T') upgrade check: $CR does not run and was not installed by uv, pipx or this script"; echo "$MSG"
      return 1
    fi
    echo "$(date '+%F %T') upgrade check: $CR does not run (${VER:-no version}): removing it ($WHO) and installing again"
    hold; remove_install "$P"; unhold; rm -f "$STAMP"; return 2
  fi
  if [ -z "$WHO" ]; then
    # installed some other way: the user upgrades it, with these steps, shown once at the next session start
    CMD="$PY -m pip install -U \"$SPEC\""
    MSG="cloakroom: version $VER is installed, but the plugin now uses $PIN, and this copy wasn't installed by uv, pipx or the plugin, so it can't upgrade itself. To finish: 1. Run: $CMD (or, with uv: uv tool install --force --python 3.12 \"$SPEC\"). 2. Then restart Claude Code (/exit, then claude). Until then version $VER keeps protecting you."
    printf '{"systemMessage": "%s"}\n' "$(json_text "$MSG")" >"$NOTICE"
    echo "$PIN" >"$STAMP"
    echo "$(date '+%F %T') upgrade check: cloakroom $VER in $PREFIX is older than $PIN but not installed by uv, pipx or this script: steps shown at the next session start"
    echo "$MSG"; return 0
  fi
  echo "$(date '+%F %T') upgrading $VER -> $PIN ($WHO: $SPEC)"
  hold
  if ! upgrade_install "$SPEC"; then
    unhold
    if "$CR" --version >/dev/null 2>&1; then
      echo "failed:$PIN" >"$STAMP"
      echo "$(date '+%F %T') upgrade failed: cloakroom $VER stays in use; it tries again in 10 minutes"; return 1
    fi
    echo "$(date '+%F %T') upgrade failed and the program no longer runs: removing it and installing again"
    hold; remove_install "$P"; unhold; rm -f "$STAMP"; return 2
  fi
  unhold
  CR="$(cloakroom_bin)"
  if [ -z "$CR" ] || ! NEW="$("$CR" --version 2>/dev/null)"; then
    echo "$(date '+%F %T') upgrade installed, but the program does not run: removing it and installing again"
    hold; remove_install "$P"; unhold; rm -f "$STAMP"; return 2
  fi
  # the running service still has the old code loaded: restart it on the new version
  n="$(stop_service "$PREFIX" "$P" "$CR")"
  if [ "$n" -gt 0 ]; then
    i=0; while [ "$i" -lt 20 ] && [ "$(stop_service "$PREFIX" "$P" "$CR")" -gt 0 ]; do sleep 0.5; i=$((i + 1)); done
    (nohup "$CR" serve </dev/null >>"$H/serve.log" 2>&1 &)
    echo "$(date '+%F %T') service restarted on the new version"
  fi
  echo "$PIN" >"$STAMP"
  echo "$(date '+%F %T') done: ${NEW:-cloakroom $PIN}"
  if [ "${PLAT%%-*}" = Darwin ]; then echo "$(date '+%F %T') macOS may ask whether 'python' can use the 'cloakroom' item in your Keychain. Click Always Allow: it only lets Cloakroom read its own key. If you deny, your own rules turn off; built-in checks keep running."; fi
}
if [ "${1:-}" = --upgrade ]; then
  upgrade >>"$LOG" 2>&1; RC=$?
  [ "$RC" = 2 ] || exit "$RC"
  WHO="" UV=""  # broken install removed: the first install, below
fi

{
  rm -f "$REASONF"
  WHO="${CLOAKROOM_INSTALLER:-}"
  if [ -z "$WHO" ]; then
    UV="$(find_uv)"
    if [ -n "$UV" ]; then WHO=uv
    elif command -v pipx >/dev/null 2>&1; then WHO=pipx
    elif fetch_uv; then WHO=uv
    elif [ -n "$(py310)" ] && "$(py310)" -c 'import venv, ensurepip' 2>/dev/null; then WHO=venv
      echo "falling back to a private virtualenv in $H/venv"
    elif [ -n "$(py310)" ]; then WHO=novenv  # Debian/Ubuntu ship python3 without venv (package python3-venv)
    else WHO=none; fi
  elif [ "$WHO" = uv ]; then
    UV="$(find_uv)"; [ -n "$UV" ] || fetch_uv || WHO=none
  fi
  echo "$(date '+%F %T') installer: $WHO${UV:+ ($UV)}${NB:+ (Intel Mac: prebuilt wheels only)}"
  if [ "$WHO" = novenv ]; then
    fail "${REASON:-nofetch}" "Python is here but its venv module isn't (Ubuntu/Debian: sudo apt install python3-venv), and uv could not be fetched"
  fi
  if [ "$WHO" = none ]; then fail "${REASON:-nofetch}" "no installer: uv could not be fetched and there is no pipx or Python 3.10+"; fi
  echo "$(date '+%F %T') stage 1 of 2: installing $RULES (rules-only protection)"
  echo rules > "$STATE"
  if ! install "$RULES" 0; then
    if online; then fail failed "stage 1 failed"; else fail offline "stage 1 failed: PyPI can't be reached"; fi
  fi
  CR="$(cloakroom_bin)"
  if [ -z "$CR" ] || ! "$CR" --version >/dev/null 2>&1; then
    remove_install; fail failed "stage 1 installed cloakroom, but the program does not run (${CR:-not found})"
  fi
  echo "$PIN" >"$STAMP"  # this plugin version's release is in, and runs: no upgrade check on the first hook call
  if [ "$INTEL_MAC" = 1 ]; then
    publish
    echo "$(date '+%F %T') Intel Mac: rules-only protection. PyTorch and ONNX Runtime publish no Intel Mac builds, so the detection model can't run here: keys, cards and IDs are masked, names and addresses are not."
    if [ -e "$H/config.json" ]; then echo model-failed > "$STATE"  # keep the user's config; the hook says rules-only
    else echo '{"mode": "rules"}' > "$H/config.json"; rm -f "$STATE"; fi
    echo "$(date '+%F %T') done: rules-only protection (Intel Mac). Program: ${CR:-unknown} (if 'cloakroom' is not found, open a new terminal or use that full path)"
    if [ -n "$CR" ]; then (nohup "$CR" serve </dev/null >>"$H/serve.log" 2>&1 &); echo "$(date '+%F %T') service starting"; fi
    exit 0
  fi
  echo model > "$STATE"
  echo "$(date '+%F %T') stage 2 of 2: installing the model ($FULL; about 1 GB on a Mac, up to about 4 GB elsewhere, of which local OCR about 100 MB)"
  OK=0
  if install "$FULL" 1; then OK=1
  else
    echo "$(date '+%F %T') the install with OCR failed: retrying without it ($NO_OCR); scanned PDFs and photos stay held back"
    install "$NO_OCR" 2 && OK=1
  fi
  CR="$(cloakroom_bin)"
  publish
  if [ "$OK" = 1 ]; then
    if [ -n "$CR" ]; then
      echo "$(date '+%F %T') building the fast CPU model if this machine has no GPU (a few minutes)"
      "$CR" export-onnx --if-no-gpu || echo "CPU model build failed: full mode still works, just slower"
    fi
    rm -f "$STATE"; echo "$(date '+%F %T') done: full protection. Program: ${CR:-unknown} (if 'cloakroom' is not found, open a new terminal or use that full path)"
    if [ "${PLAT%%-*}" = Darwin ]; then echo "$(date '+%F %T') macOS may ask whether 'python' can use the 'cloakroom' item in your Keychain. Click Always Allow: it only lets Cloakroom read its own key. If you deny, your own rules turn off; built-in checks keep running."; fi
    # start the service now, so the user's next prompt doesn't wait for the model's first load
    if [ -n "$CR" ]; then (nohup "$CR" serve </dev/null >>"$H/serve.log" 2>&1 &); echo "$(date '+%F %T') service starting"; fi
  else
    echo model-failed > "$STATE"; echo "$(date '+%F %T') stage 2 failed: staying on rules-only protection"; steps model
  fi
} >>"$LOG" 2>&1
