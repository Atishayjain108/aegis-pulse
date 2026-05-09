#!/usr/bin/env bash
# =============================================================================
# AEGIS PULSE OMEGA v2 — 04_services.sh
# -----------------------------------------------------------------------------
# Validates Docker Desktop WSL integration and installs:
#   - Playwright browsers (Chromium + Firefox) with all runtime libs
#   - Ollama (gated behind --enable-ollama OR AEGIS_ENABLE_OLLAMA=1,
#     since the default 14B model needs ~8 GB VRAM)
#
# Does NOT install Docker itself — Docker Desktop on Windows provides the
# daemon, and the `docker` CLI is injected into WSL by the integration.
#
# Flags:
#   --enable-ollama    Install Ollama service and pull default models.
#   --skip-playwright  Skip Playwright browser download (useful in CI).
# =============================================================================

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
# shellcheck source=./lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

aegis_enable_errtrap
require_wsl
require_not_root

ENABLE_OLLAMA=0
SKIP_PLAYWRIGHT=0
[[ "${AEGIS_ENABLE_OLLAMA:-0}" == "1" ]] && ENABLE_OLLAMA=1
for arg in "$@"; do
  case "$arg" in
    --enable-ollama)    ENABLE_OLLAMA=1 ;;
    --skip-playwright)  SKIP_PLAYWRIGHT=1 ;;
    -h|--help) sed -n '3,20p' "$0"; exit 0 ;;
    *) log_warn "Unknown flag: $arg" ;;
  esac
done

log_section "AEGIS Bootstrap — Phase 04: Services (Docker / Playwright / Ollama)"

# Ensure uv/pyenv are in PATH in case user didn't reopen shell.
export PATH="$HOME/.local/bin:$HOME/.pyenv/bin:$PATH"
if [[ -d "$HOME/.pyenv" ]]; then
  eval "$(pyenv init - bash)" 2>/dev/null || true
fi

# -----------------------------------------------------------------------------
# Docker — verify Desktop's WSL integration works
# -----------------------------------------------------------------------------
check_docker() {
  if ! cmd_exists docker; then
    log_error "'docker' CLI not found in WSL."
    log_error "Fix: In Docker Desktop → Settings → Resources → WSL Integration,"
    log_error "     enable the Ubuntu-24.04 distro, click Apply & Restart, then"
    log_error "     reopen this terminal and re-run: bash bootstrap/wsl/04_services.sh"
    die "$EXIT_DOCKER_FAIL" "Docker CLI missing. See docs/errors/AEGIS-BOOT-0023.md"
  fi

  if ! docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
    log_error "'docker' present but cannot reach the daemon."
    log_error "Fix: start Docker Desktop on Windows, wait until the whale icon"
    log_error "     is steady green, then re-run this script."
    die "$EXIT_DOCKER_FAIL" "Docker daemon unreachable. See docs/errors/AEGIS-BOOT-0023.md"
  fi

  local server
  server=$(docker version --format '{{.Server.Version}}')
  log_success "Docker daemon reachable. Server: $server"

  # Hello world — catches weird WSL mounting issues proactively.
  log_info "Running 'docker run --rm hello-world' ..."
  if ! retry 3 5 -- docker run --rm hello-world >/dev/null 2>&1; then
    log_warn "hello-world pull failed. Registry rate-limit? Try: docker login"
  else
    log_success "Docker run smoke test passed."
  fi

  # Compose v2 check.
  if docker compose version >/dev/null 2>&1; then
    log_success "Docker Compose v2: $(docker compose version --short)"
  else
    log_warn "'docker compose' not found. Install Docker Desktop ≥ 4.24."
  fi
}

run_step "check_docker" check_docker

# -----------------------------------------------------------------------------
# Playwright — browsers + OS libs
# -----------------------------------------------------------------------------
install_playwright() {
  if [[ "$SKIP_PLAYWRIGHT" == "1" ]]; then
    log_warn "Skipping Playwright install (--skip-playwright)."
    return 0
  fi

  # Install Playwright in a throwaway venv just to invoke `playwright install`.
  # The real project will install its own pinned version; here we only need the
  # OS-level browsers + shared libs (which are user-global, cached in ~/.cache/ms-playwright).
  local tmpd
  tmpd=$(mktemp -d)
  (
    cd "$tmpd" || exit 1
    uv venv --python "$AEGIS_PYTHON_VERSION" .venv >/dev/null
    # shellcheck disable=SC1091
    source .venv/bin/activate
    retry 3 5 -- uv pip install --quiet "playwright==1.47.0"
    # --with-deps pulls every apt lib Chromium/Firefox need on Ubuntu 24.04.
    # The `patchright` stealth fork we use later in Phase 1 shares these libs.
    log_info "Downloading Chromium + Firefox (~400 MB, takes a few minutes)..."
    retry 2 10 -- sudo "$(command -v python)" -m playwright install-deps chromium firefox
    retry 2 10 -- playwright install chromium firefox
  )
  sudo rm -rf "$tmpd"

  # Verify browsers are where Playwright expects them.
  local cache="$HOME/.cache/ms-playwright"
  if [[ -d "$cache" ]] && compgen -G "$cache/chromium-*" >/dev/null; then
    log_success "Playwright browsers installed → $cache"
  else
    log_warn "Expected $cache/chromium-* but did not find it. Re-run with AEGIS_FORCE=1."
  fi
}

run_step "install_playwright" install_playwright

# -----------------------------------------------------------------------------
# Ollama (optional, gated)
# -----------------------------------------------------------------------------
install_ollama() {
  if [[ "$ENABLE_OLLAMA" != "1" ]]; then
    log_info "Ollama install skipped (default). Enable with --enable-ollama."
    log_info "Ollama needs ~8 GB VRAM for qwen2.5:14b, or ~6 GB for llama3.3:8b."
    return 0
  fi

  if cmd_exists ollama; then
    log_info "Ollama already installed: $(ollama --version 2>&1)"
  else
    log_info "Installing Ollama (official installer)..."
    # Pinned via the official install script (which pulls latest stable).
    # We verify post-install; if the user wants pinning, they can override
    # with OLLAMA_VERSION before running this script.
    retry 3 5 -- bash -c "curl -fsSL https://ollama.com/install.sh | sh"
  fi

  # Ensure service is running. Installer sets up systemd unit; if systemd is
  # not enabled in /etc/wsl.conf yet, fall back to foreground background.
  if pidof systemd >/dev/null 2>&1; then
    sudo systemctl enable --now ollama || log_warn "systemctl enable ollama failed."
  else
    log_warn "systemd not active — starting ollama in background (nohup)."
    log_warn "For persistence, run 'wsl --shutdown' after 01_system.sh, then re-run this script."
    pgrep -x ollama >/dev/null || nohup ollama serve >"$AEGIS_HOME/ollama.log" 2>&1 &
    sleep 2
  fi

  # Smoke test — list models (requires API to be up).
  if retry 5 3 -- bash -c 'curl -sf http://127.0.0.1:11434/api/tags >/dev/null'; then
    log_success "Ollama API reachable at http://127.0.0.1:11434"
  else
    log_warn "Ollama API not responding. Check: journalctl -u ollama -n 50"
    return 0
  fi

  # Pull default models if GPU available. On CPU, only pull phi-4 (small).
  local models=()
  if [[ "$(state_get has_gpu)" == "1" ]]; then
    models=("qwen2.5:14b" "llama3.3:8b" "phi-4")
  else
    log_info "No GPU — pulling only phi-4 for CPU use."
    models=("phi-4")
  fi
  for m in "${models[@]}"; do
    if ollama list 2>/dev/null | awk '{print $1}' | grep -qxF "$m"; then
      log_info "Ollama model present: $m"
    else
      log_info "Pulling Ollama model: $m (several GB — be patient)..."
      retry 2 10 -- ollama pull "$m" || log_warn "Failed to pull $m (non-fatal)."
    fi
  done
}

run_step "install_ollama" install_ollama

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
log_section "Phase 04 complete"
cmd_exists docker    && log_info "Docker:     $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo 'daemon down')"
cmd_exists ollama    && log_info "Ollama:     $(ollama --version 2>&1 | head -1)"
# shellcheck disable=SC2012  # diagnostic one-liner; directory contents are all ASCII browser names
[[ -d "$HOME/.cache/ms-playwright" ]] && log_info "Playwright: $(ls -1 "$HOME/.cache/ms-playwright" 2>/dev/null | wc -l) browser bundles cached"
log_success "Phase 04 done."
