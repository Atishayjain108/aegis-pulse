#!/usr/bin/env bash
# =============================================================================
# AEGIS PULSE OMEGA v2 — Shared bootstrap library
# -----------------------------------------------------------------------------
# Sourced by every bootstrap/wsl/*.sh script. Provides:
#   - Structured logging to ~/.aegis/bootstrap.log AND stdout
#   - Idempotency via a simple state file (~/.aegis/state.env)
#   - Environment guards (WSL only, non-root, Ubuntu 24.04)
#   - Retry with decorrelated jitter for network ops
#   - Consistent exit codes (see docs/errors/AEGIS-BOOT-*.md)
#
# DO NOT run this file directly. It is library-only.
# =============================================================================

# Guard against double-sourcing (scripts may indirectly source this twice).
if [[ -n "${__AEGIS_COMMON_LOADED:-}" ]]; then
  return 0
fi
__AEGIS_COMMON_LOADED=1

# -----------------------------------------------------------------------------
# Constants (intentionally declared at library level for sourcing scripts —
# linter "unused" warnings are suppressed because they ARE used in 02..05).
# -----------------------------------------------------------------------------
# shellcheck disable=SC2034
{
readonly AEGIS_HOME="${AEGIS_HOME:-$HOME/.aegis}"
readonly AEGIS_LOG_FILE="${AEGIS_LOG_FILE:-$AEGIS_HOME/bootstrap.log}"
readonly AEGIS_STATE_FILE="${AEGIS_STATE_FILE:-$AEGIS_HOME/state.env}"
readonly AEGIS_CONFIG_FILE="${AEGIS_CONFIG_FILE:-$AEGIS_HOME/config.env}"

readonly AEGIS_PYTHON_VERSION="3.12.7"
readonly AEGIS_UV_VERSION="0.4.30"
readonly AEGIS_PYENV_VERSION="2.4.17"
readonly AEGIS_CUDA_VERSION_MAJOR="12"
readonly AEGIS_CUDA_VERSION_MINOR="4"
readonly AEGIS_UBUNTU_VERSION="24.04"

# Exit codes — see docs/errors/AEGIS-BOOT-*.md
readonly EXIT_OK=0
readonly EXIT_USAGE=2
readonly EXIT_NOT_WSL=10
readonly EXIT_WRONG_UBUNTU=11
readonly EXIT_IS_ROOT=12
readonly EXIT_NO_INTERNET=13
readonly EXIT_APT_FAIL=20
readonly EXIT_PYTHON_FAIL=21
readonly EXIT_GPU_FAIL=22
readonly EXIT_DOCKER_FAIL=23
readonly EXIT_GENERIC=99
}

# Colour codes (only enabled if stdout is a TTY).
if [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && [[ $(tput colors 2>/dev/null || echo 0) -ge 8 ]]; then
  readonly C_RESET=$(tput sgr0)
  readonly C_RED=$(tput setaf 1)
  readonly C_GREEN=$(tput setaf 2)
  readonly C_YELLOW=$(tput setaf 3)
  readonly C_BLUE=$(tput setaf 4)
  readonly C_MAGENTA=$(tput setaf 5)
  readonly C_CYAN=$(tput setaf 6)
  readonly C_BOLD=$(tput bold)
else
  readonly C_RESET="" C_RED="" C_GREEN="" C_YELLOW=""
  readonly C_BLUE="" C_MAGENTA="" C_CYAN="" C_BOLD=""
fi

# -----------------------------------------------------------------------------
# Bootstrap log directory (safe even before anything else runs)
# -----------------------------------------------------------------------------
mkdir -p "$AEGIS_HOME"
touch "$AEGIS_LOG_FILE"
[[ -f "$AEGIS_STATE_FILE"  ]] || : > "$AEGIS_STATE_FILE"
[[ -f "$AEGIS_CONFIG_FILE" ]] || : > "$AEGIS_CONFIG_FILE"

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
# All log_* functions write to both the log file (plain) and stdout (coloured).
# Format: ISO8601 | LEVEL | SCRIPT | message

__aegis_script_name() {
  # Use $0 of the top-level script if available, fall back to BASH_SOURCE.
  local s="${0##*/}"
  [[ "$s" == "bash" || "$s" == "-bash" ]] && s="${BASH_SOURCE[-1]##*/}"
  printf '%s' "$s"
}

__aegis_log_raw() {
  # Args: LEVEL COLOUR MESSAGE...
  local level="$1" colour="$2"; shift 2
  local ts script line
  ts=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
  script=$(__aegis_script_name)
  line="$ts | ${level} | ${script} | $*"
  printf '%s\n' "$line" >> "$AEGIS_LOG_FILE"
  printf '%s[%s]%s %s\n' "$colour" "$level" "$C_RESET" "$*"
}

log_info()    { __aegis_log_raw "INFO " "$C_BLUE"    "$@"; }
log_warn()    { __aegis_log_raw "WARN " "$C_YELLOW"  "$@"; }
log_error()   { __aegis_log_raw "ERROR" "$C_RED"     "$@" >&2; }
log_success() { __aegis_log_raw "OK   " "$C_GREEN"   "$@"; }
# shellcheck disable=SC2015  # intentional: the `|| true` forces rc=0 so set -e never fires when debug is off
log_debug()   { [[ "${AEGIS_DEBUG:-0}" == "1" ]] && __aegis_log_raw "DEBUG" "$C_MAGENTA" "$@" || true; }

log_section() {
  # Emphasised section header. Args: title
  local title="$1"
  local bar="========================================================================"
  printf '\n%s%s%s\n' "$C_CYAN$C_BOLD" "$bar" "$C_RESET"
  printf '%s%s%s\n'   "$C_CYAN$C_BOLD" "  $title"   "$C_RESET"
  printf '%s%s%s\n\n' "$C_CYAN$C_BOLD" "$bar" "$C_RESET"
  { echo; echo "$bar"; echo "  $title"; echo "$bar"; echo; } >> "$AEGIS_LOG_FILE"
}

die() {
  # Args: EXIT_CODE MESSAGE...
  local code="$1"; shift
  log_error "$@"
  log_error "Exiting with code $code. See $AEGIS_LOG_FILE for details."
  exit "$code"
}

# -----------------------------------------------------------------------------
# Error trap — prints the failing line when set -e kicks in
# -----------------------------------------------------------------------------
aegis_enable_errtrap() {
  set -Eeuo pipefail
  trap '__aegis_errtrap $? $LINENO "$BASH_COMMAND"' ERR
}

__aegis_errtrap() {
  local code="$1" line="$2" cmd="$3"
  log_error "Unhandled failure at line $line (exit $code): $cmd"
  log_error "Full log: $AEGIS_LOG_FILE"
  exit "$code"
}

# -----------------------------------------------------------------------------
# State file (idempotency) — simple KEY=VALUE lines
# -----------------------------------------------------------------------------
state_get() {
  # Args: key  → prints value (empty if unset)
  local key="$1"
  grep -E "^${key}=" "$AEGIS_STATE_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- || true
}

state_set() {
  # Args: key value
  local key="$1" value="$2"
  local tmp
  tmp=$(mktemp)
  # Remove any previous entry for this key, then append.
  grep -v -E "^${key}=" "$AEGIS_STATE_FILE" > "$tmp" || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$AEGIS_STATE_FILE"
}

state_done() {
  # Args: key  → 0 if state[key]==done, else 1
  [[ "$(state_get "$1")" == "done" ]]
}

# run_step: skips a step if already marked done (unless AEGIS_FORCE=1).
# Usage:
#   run_step "01_apt_base" install_apt_packages
run_step() {
  local key="$1"; shift
  if state_done "$key" && [[ "${AEGIS_FORCE:-0}" != "1" ]]; then
    log_info "Skipping step '$key' (already done). Set AEGIS_FORCE=1 to rerun."
    return 0
  fi
  log_section "STEP: $key"
  local start end
  start=$(date +%s)
  "$@"
  end=$(date +%s)
  state_set "$key" "done"
  log_success "Step '$key' completed in $((end - start))s"
}

# -----------------------------------------------------------------------------
# Environment guards
# -----------------------------------------------------------------------------
is_wsl() {
  # Multiple detection paths: /proc/version, /proc/sys/kernel/osrelease, env vars.
  if [[ -n "${WSL_DISTRO_NAME:-}" || -n "${WSL_INTEROP:-}" ]]; then
    return 0
  fi
  if grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
    return 0
  fi
  return 1
}

require_wsl() {
  is_wsl || die "$EXIT_NOT_WSL" \
    "This script must run inside WSL2 Ubuntu. See docs/errors/AEGIS-BOOT-0010.md"
}

require_not_root() {
  if [[ $EUID -eq 0 ]]; then
    die "$EXIT_IS_ROOT" \
      "Do not run as root. Run as your normal WSL user; sudo is invoked where needed. See docs/errors/AEGIS-BOOT-0012.md"
  fi
}

require_ubuntu() {
  local want="${1:-$AEGIS_UBUNTU_VERSION}"
  if [[ ! -r /etc/os-release ]]; then
    die "$EXIT_WRONG_UBUNTU" "/etc/os-release not found — is this really Ubuntu?"
  fi
  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    die "$EXIT_WRONG_UBUNTU" "Expected Ubuntu, found: ${ID:-unknown}. See docs/errors/AEGIS-BOOT-0011.md"
  fi
  if [[ "${VERSION_ID:-}" != "$want" ]]; then
    log_warn "Expected Ubuntu $want, found ${VERSION_ID:-unknown}. Continuing but unsupported."
  fi
}

require_internet() {
  local url="${1:-https://pypi.org}"
  if ! curl --silent --head --fail --max-time 10 "$url" >/dev/null 2>&1; then
    die "$EXIT_NO_INTERNET" \
      "No internet connectivity to $url. Check WSL DNS (see docs/errors/AEGIS-BOOT-0013.md)."
  fi
}

# -----------------------------------------------------------------------------
# Small utilities
# -----------------------------------------------------------------------------
cmd_exists() { command -v "$1" >/dev/null 2>&1; }

prompt_yes_no() {
  # Args: question [default=N]
  local question="$1" default="${2:-N}" reply
  local hint="[y/N]"
  [[ "$default" == "Y" ]] && hint="[Y/n]"
  # Auto-yes when AEGIS_NONINTERACTIVE=1 (CI, re-runs).
  if [[ "${AEGIS_NONINTERACTIVE:-0}" == "1" ]]; then
    [[ "$default" == "Y" ]]
    return
  fi
  read -r -p "$question $hint " reply
  reply="${reply:-$default}"
  [[ "${reply^^}" == "Y" || "${reply^^}" == "YES" ]]
}

# Retry with decorrelated jitter (AWS recipe).
#   retry <max_attempts> <base_sleep_s> -- <cmd> <args...>
retry() {
  local attempts="$1" base="$2"; shift 2
  [[ "$1" == "--" ]] && shift
  local n=1 sleep_s="$base"
  while true; do
    if "$@"; then
      return 0
    fi
    if (( n >= attempts )); then
      log_error "Command failed after $attempts attempts: $*"
      return 1
    fi
    # Decorrelated jitter: sleep = random(base, prev*3), capped at 60s.
    local upper=$(( sleep_s * 3 > 60 ? 60 : sleep_s * 3 ))
    sleep_s=$(( RANDOM % (upper - base + 1) + base ))
    log_warn "Attempt $n/$attempts failed. Retrying in ${sleep_s}s: $*"
    sleep "$sleep_s"
    (( n++ ))
  done
}

ensure_dir() {
  local d="$1" mode="${2:-0755}"
  mkdir -p "$d"
  chmod "$mode" "$d"
}

# Append a line to a file only if it's not already present. Idempotent.
# Usage: append_line_once <file> <line>
append_line_once() {
  local file="$1" line="$2"
  # Create file if missing (respects sudo if needed).
  if [[ ! -f "$file" ]]; then
    if [[ -w "$(dirname "$file")" ]]; then
      : > "$file"
    else
      sudo touch "$file"
    fi
  fi
  if ! grep -qxF -- "$line" "$file" 2>/dev/null; then
    if [[ -w "$file" ]]; then
      printf '%s\n' "$line" >> "$file"
    else
      printf '%s\n' "$line" | sudo tee -a "$file" >/dev/null
    fi
    log_debug "Appended to $file: $line"
  else
    log_debug "Line already present in $file: $line"
  fi
}

# sudo_keep_alive: prompts for password once, keeps ticket alive in background.
# Auto-killed on script exit via trap.
sudo_keep_alive() {
  if ! sudo -n true 2>/dev/null; then
    log_info "Requesting sudo (will be cached for this script run)..."
    sudo -v
  fi
  (while true; do sudo -n true; sleep 50; kill -0 "$$" 2>/dev/null || exit; done) &
  local keep_alive_pid=$!
  # shellcheck disable=SC2064  # we WANT $keep_alive_pid to expand now (it's local)
  trap "kill ${keep_alive_pid} 2>/dev/null || true" EXIT
}

# Detect if NVIDIA GPU is available from WSL. Sets global AEGIS_HAS_GPU=0|1.
detect_gpu() {
  if cmd_exists nvidia-smi && nvidia-smi -L >/dev/null 2>&1; then
    AEGIS_HAS_GPU=1
    log_info "NVIDIA GPU detected: $(nvidia-smi -L | head -1)"
  else
    AEGIS_HAS_GPU=0
    log_info "No NVIDIA GPU detected — CPU-only path will be used."
  fi
  state_set "has_gpu" "$AEGIS_HAS_GPU"
  export AEGIS_HAS_GPU
}

# Detect WSL kernel features.
wsl_kernel_version() {
  uname -r | awk -F- '{print $1}'
}

# Write a key=value pair into ~/.aegis/config.env (used by later stages / runtime).
config_set() {
  local key="$1" value="$2"
  local tmp
  tmp=$(mktemp)
  grep -v -E "^${key}=" "$AEGIS_CONFIG_FILE" > "$tmp" || true
  printf '%s=%q\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$AEGIS_CONFIG_FILE"
}
