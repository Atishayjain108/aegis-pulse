#!/usr/bin/env bash
# =============================================================================
# AEGIS PULSE OMEGA v2 — 02_python.sh
# -----------------------------------------------------------------------------
# Installs pyenv, Python 3.12.7 (exact), and uv (Astral's Rust-based package
# manager). Both user-local — no sudo after this point in this script.
#
# Rationale for pinning:
#   - Python 3.12.7: stable Q3 2024 release, no GIL-removal experiments,
#     broadest wheel availability as of Q2 2026.
#   - pyenv 2.4.17: last release known to handle 3.12 build on Ubuntu 24.04
#     without OpenSSL 3 quirks.
#   - uv 0.4.30: introduces full --python-preference=managed support we rely on.
# =============================================================================

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
# shellcheck source=./lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

aegis_enable_errtrap
require_wsl
require_not_root
require_ubuntu "$AEGIS_UBUNTU_VERSION"

log_section "AEGIS Bootstrap — Phase 02: Python $AEGIS_PYTHON_VERSION + uv $AEGIS_UV_VERSION"

require_internet "https://pypi.org"

# -----------------------------------------------------------------------------
# pyenv install — clone repo, NOT the apt package (the apt one is stale)
# -----------------------------------------------------------------------------
PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"

install_pyenv() {
  if [[ -d "$PYENV_ROOT/.git" ]]; then
    log_info "pyenv repo present at $PYENV_ROOT — updating."
    git -C "$PYENV_ROOT" fetch --tags --quiet
  else
    log_info "Cloning pyenv → $PYENV_ROOT"
    retry 3 5 -- git clone --quiet https://github.com/pyenv/pyenv.git "$PYENV_ROOT"
  fi
  # Checkout the pinned release tag.
  git -C "$PYENV_ROOT" checkout --quiet "v$AEGIS_PYENV_VERSION"
  # Build the bundled pyenv C helpers for speed (non-fatal if cc missing).
  ( cd "$PYENV_ROOT" && src/configure && make -C src >/dev/null 2>&1 ) || \
    log_warn "pyenv native ext build failed — pyenv will still work, just slower."
}

run_step "install_pyenv" install_pyenv

# -----------------------------------------------------------------------------
# Shell init — bashrc + zshrc
# -----------------------------------------------------------------------------
configure_shell_init() {
  # NOTE: the block below is single-quoted on purpose — $PYENV_ROOT et al. must
  # remain as literal strings so they expand in the user's future shells, not now.
  # shellcheck disable=SC2016
  local block='# >>> AEGIS PYENV/UV INIT (managed by 02_python.sh) >>>
export PYENV_ROOT="$HOME/.pyenv"
[[ -d "$PYENV_ROOT/bin" ]] && export PATH="$PYENV_ROOT/bin:$PATH"
if command -v pyenv >/dev/null 2>&1; then
  eval "$(pyenv init - bash)"
fi
# uv (Astral) — user install
export PATH="$HOME/.local/bin:$PATH"
# direnv
if command -v direnv >/dev/null 2>&1; then
  eval "$(direnv hook bash)"
fi
# <<< AEGIS PYENV/UV INIT <<<'

  for rcfile in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
    [[ -f "$rcfile" ]] || touch "$rcfile"
    if ! grep -q "AEGIS PYENV/UV INIT" "$rcfile"; then
      printf '\n%s\n' "$block" >> "$rcfile"
      log_info "Added pyenv/uv/direnv init block to $rcfile"
    else
      log_debug "Init block already present in $rcfile"
    fi
  done
}

run_step "configure_shell_init" configure_shell_init

# Make pyenv available in THIS shell for the remaining steps.
export PYENV_ROOT
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init - bash)"

# -----------------------------------------------------------------------------
# Install Python 3.12.7
# -----------------------------------------------------------------------------
install_python() {
  local want="$AEGIS_PYTHON_VERSION"
  if pyenv versions --bare | grep -qE "^${want}\$"; then
    log_info "Python $want already installed via pyenv."
  else
    log_info "Compiling Python $want — this takes 3–6 minutes on a laptop..."
    # Flags: shared lib (needed by some extensions), optimized build.
    PYTHON_CONFIGURE_OPTS="--enable-shared --enable-optimizations --with-lto" \
    PYTHON_CFLAGS="-march=native -O2" \
    MAKE_OPTS="-j$(nproc)" \
    retry 2 10 -- pyenv install --skip-existing "$want"
  fi
  pyenv global "$want"
  hash -r  # refresh command cache
  # Verify.
  local actual
  actual=$(python --version 2>&1 | awk '{print $2}')
  if [[ "$actual" != "$want" ]]; then
    die "$EXIT_PYTHON_FAIL" "Expected Python $want globally, got '$actual'. Check PATH."
  fi
  log_success "Python $actual is now the global default."
}

run_step "install_python" install_python

# -----------------------------------------------------------------------------
# Install uv (Astral, Rust-based, 10-100x faster than pip, deterministic lockfiles)
# -----------------------------------------------------------------------------
install_uv() {
  local want="0.11.7"
  if cmd_exists uv; then
    local actual
    actual=$(uv --version 2>/dev/null | awk '{print $2}' || echo "")
    if [[ "$actual" == "$want" ]]; then
      log_info "uv $want already installed."
      return 0
    fi
    log_info "uv present at version $actual — reinstalling $want."
  fi
  # Official installer — pinned to a specific release.
  local url="https://github.com/astral-sh/uv/releases/download/${want}/uv-installer.sh"
  log_info "Installing uv $want from $url"
  retry 3 5 -- bash -c "curl -LsSf '$url' | env UV_INSTALL_DIR='$HOME/.local/bin' sh"
  export PATH="$HOME/.local/bin:$PATH"
  hash -r
  local actual
  actual=$(uv --version 2>/dev/null | awk '{print $2}' || echo "")
  [[ "$actual" == "$want" ]] || die "$EXIT_PYTHON_FAIL" "uv install verification failed. Got: $actual"
  log_success "uv $actual installed."
}

run_step "install_uv" install_uv

# -----------------------------------------------------------------------------
# Sanity check — end-to-end: can we create a venv and pip-install something?
# -----------------------------------------------------------------------------
smoke_test_python() {
  local tmpd
  tmpd=$(mktemp -d)
  (
    cd "$tmpd" || exit 1
    uv venv --python "$AEGIS_PYTHON_VERSION" .venv >/dev/null 2>&1
    # shellcheck disable=SC1091
    source .venv/bin/activate
    uv pip install --quiet "requests==2.32.3"
    python -c 'import requests, sys; assert sys.version_info[:3]==(3,12,7), sys.version; print("smoke OK"); print(requests.__version__)'
  )
  local rc=$?
  rm -rf "$tmpd"
  [[ $rc -eq 0 ]] || die "$EXIT_PYTHON_FAIL" "Python/uv smoke test failed — see $AEGIS_LOG_FILE"
  log_success "Python + uv smoke test passed."
}

run_step "smoke_test_python" smoke_test_python

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
log_section "Phase 02 complete"
log_info "Python:   $(python --version 2>&1)"
log_info "pyenv:    $(pyenv --version 2>&1)"
log_info "uv:       $(uv --version 2>&1)"
log_info "Global:   $(pyenv version)"
log_info "Paths:    PYENV_ROOT=$PYENV_ROOT ; uv=$(command -v uv)"
log_success "Phase 02 done. Open a new shell OR 'source ~/.bashrc' before continuing."
