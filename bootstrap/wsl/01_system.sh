#!/usr/bin/env bash
# =============================================================================
# AEGIS PULSE OMEGA v2 — 01_system.sh
# -----------------------------------------------------------------------------
# Installs OS-level prerequisites and fixes WSL gotchas documented in
# docs/errors/AEGIS-BOOT-*.md. Idempotent: safe to re-run.
#
# Usage:  bash bootstrap/wsl/01_system.sh
# Env:    AEGIS_FORCE=1           re-run completed steps
#         AEGIS_NONINTERACTIVE=1  no prompts (CI mode)
#         AEGIS_DEBUG=1           verbose logging
# =============================================================================

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
# shellcheck source=./lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

aegis_enable_errtrap
require_wsl
require_not_root
require_ubuntu "$AEGIS_UBUNTU_VERSION"

log_section "AEGIS Bootstrap — Phase 01: System base"

sudo_keep_alive
require_internet "https://archive.ubuntu.com"

# -----------------------------------------------------------------------------
# APT base update (retried — WSL's NAT can flap mid-download)
# -----------------------------------------------------------------------------
apt_update() {
  retry 3 5 -- sudo apt-get update -y
}

apt_upgrade() {
  # --with-new-pkgs: allow pulling new deps added by security updates.
  retry 3 5 -- sudo DEBIAN_FRONTEND=noninteractive apt-get \
    -o Dpkg::Options::="--force-confdef" \
    -o Dpkg::Options::="--force-confold" \
    upgrade -y --with-new-pkgs
}

run_step "apt_update"  apt_update
run_step "apt_upgrade" apt_upgrade

# -----------------------------------------------------------------------------
# Core system packages — every lib any later phase might need to build wheels
# -----------------------------------------------------------------------------
# Grouped for readability; flat list is what apt actually gets.
APT_PACKAGES=(
  # Build essentials
  build-essential pkg-config make cmake ninja-build gcc g++

  # Compression / archiving
  bzip2 xz-utils zip unzip p7zip-full

  # Networking / TLS / DNS
  ca-certificates curl wget gnupg lsb-release apt-transport-https
  dnsutils iputils-ping net-tools
  openssl libssl-dev libffi-dev

  # Python build prerequisites (pyenv needs these to compile 3.12.x)
  libbz2-dev libreadline-dev libsqlite3-dev libncurses-dev
  libncursesw5-dev tk-dev liblzma-dev uuid-dev zlib1g-dev

  # DB / image / XML libs — many Python wheels dlopen these
  libpq-dev libjpeg-dev libpng-dev libtiff-dev
  libxml2-dev libxslt1-dev libyaml-dev

  # Graphics / GI for Playwright + some scientific stack
  libcairo2-dev libgirepository1.0-dev
  libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2
  libgbm1 libpango-1.0-0 libasound2t64

  # Shell / productivity
  git git-lfs jq yq htop btop tmux zsh tree ripgrep fd-find bat fzf
  direnv
  # NOTE: `mkcert` installed via binary in 05_repo.sh (no apt package on 24.04).

  # Time sync — critical: WSL clock drifts after host sleep, breaking TLS
  chrony

  # Process / observability helpers
  strace lsof iproute2 sysstat

  # Crypto tooling for secrets handling (SOPS etc.)
  age
)

apt_install_packages() {
  # Single transaction is faster and atomic.
  retry 3 5 -- sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${APT_PACKAGES[@]}"
}

run_step "apt_install_base" apt_install_packages

# -----------------------------------------------------------------------------
# /etc/wsl.conf — systemd, DNS hardening, interop tuning
# -----------------------------------------------------------------------------
install_wsl_conf() {
  local src="$SCRIPT_DIR/conf/wsl.conf"
  local dst="/etc/wsl.conf"
  if [[ ! -f "$src" ]]; then
    die "$EXIT_GENERIC" "Missing template: $src"
  fi
  if [[ -f "$dst" ]] && sudo diff -q "$src" "$dst" >/dev/null 2>&1; then
    log_info "/etc/wsl.conf already current."
    return 0
  fi
  # Backup existing.
  if [[ -f "$dst" ]]; then
    local backup="/etc/wsl.conf.bak.$(date +%s)"
    sudo cp -a "$dst" "$backup"
    log_info "Backed up existing /etc/wsl.conf → $backup"
  fi
  sudo install -m 0644 -o root -g root "$src" "$dst"
  log_success "Installed /etc/wsl.conf — run 'wsl --shutdown' from PowerShell after bootstrap completes."
  state_set "wsl_conf_needs_restart" "1"
}

run_step "install_wsl_conf" install_wsl_conf

# -----------------------------------------------------------------------------
# DNS hardening — fixes the WSL "no name resolution after Windows sleep" bug
# Replace the stub resolv.conf with systemd-resolved.
# -----------------------------------------------------------------------------
configure_dns() {
  # Only take over resolv.conf if generateResolvConf was disabled (which our
  # wsl.conf does). Use a direct 1.1.1.1/8.8.8.8 fallback — we are NOT relying
  # on systemd-resolved here because on some Windows host configurations it
  # fails to pick up VPN DNS. Simple static resolv.conf is the most resilient.
  local resolv="/etc/resolv.conf"
  local desired="# AEGIS PULSE OMEGA v2 — managed file
# Do not edit; overwritten by bootstrap/wsl/01_system.sh.
nameserver 1.1.1.1
nameserver 1.0.0.1
nameserver 8.8.8.8
options edns0 trust-ad
search ."

  # Remove the symlink WSL may have left behind.
  if [[ -L "$resolv" ]]; then
    sudo rm -f "$resolv"
  fi
  # Write new file.
  printf '%s\n' "$desired" | sudo tee "$resolv" >/dev/null
  sudo chmod 0644 "$resolv"

  # Make resolv.conf immutable so WSL cannot regenerate it on restart.
  # `chattr +i` is POSIX-safe on ext4; we undo it if the user re-runs.
  sudo chattr -i "$resolv" 2>/dev/null || true
  sudo chattr +i "$resolv" 2>/dev/null || log_warn "chattr +i on /etc/resolv.conf failed — DNS may reset after WSL restart."

  # Smoke test
  if getent hosts pypi.org >/dev/null 2>&1; then
    log_success "DNS resolution working (pypi.org resolved)."
  else
    log_warn "DNS test failed — see docs/errors/AEGIS-BOOT-0013.md"
  fi
}

run_step "configure_dns" configure_dns

# -----------------------------------------------------------------------------
# NTP / time sync — WSL clock drifts after every host sleep. Chrony fixes it.
# -----------------------------------------------------------------------------
configure_time_sync() {
  # chrony was installed above. Enable + start only if systemd is active.
  if ! pidof systemd >/dev/null 2>&1; then
    log_warn "systemd is not running yet (needs wsl --shutdown after /etc/wsl.conf update). Skipping chrony enable."
    return 0
  fi
  sudo systemctl enable --now chrony || log_warn "chrony enable failed (non-fatal)."
  # Force an immediate sync.
  sudo chronyc -a makestep >/dev/null 2>&1 || true
  local offset
  offset=$(chronyc tracking 2>/dev/null | awk -F': ' '/Last offset/ {print $2}' || echo "unknown")
  log_info "Chrony offset: $offset"
}

run_step "configure_time_sync" configure_time_sync

# -----------------------------------------------------------------------------
# Sysctl — inotify watches, file descriptors, core/vm tuning for containers
# -----------------------------------------------------------------------------
configure_sysctl() {
  local f="/etc/sysctl.d/99-aegis.conf"
  sudo tee "$f" >/dev/null <<'EOF'
# AEGIS PULSE OMEGA v2 — kernel tuning
# Managed by bootstrap/wsl/01_system.sh. Edit bootstrap instead of this file.

# Inotify: VS Code + Docker + watchers exhaust the default 8192 trivially.
fs.inotify.max_user_watches = 524288
fs.inotify.max_user_instances = 8192

# File descriptors — asyncio scrapers open thousands of sockets concurrently.
fs.file-max = 2097152

# VM — keep swap use low on a laptop; we'd rather OOM and autorestart.
vm.swappiness = 10
vm.max_map_count = 262144

# Network — lifted caps for the scraper workload.
net.core.somaxconn = 4096
net.core.netdev_max_backlog = 5000
net.ipv4.tcp_max_syn_backlog = 4096
net.ipv4.tcp_tw_reuse = 1
net.ipv4.ip_local_port_range = 10240 65535
EOF
  sudo sysctl --system >/dev/null 2>&1 || log_warn "sysctl --system produced warnings (non-fatal in WSL)."
  log_success "Installed $f"
}

run_step "configure_sysctl" configure_sysctl

# -----------------------------------------------------------------------------
# ulimit — /etc/security/limits.conf bump for open files
# -----------------------------------------------------------------------------
configure_limits() {
  local f="/etc/security/limits.d/99-aegis.conf"
  sudo tee "$f" >/dev/null <<'EOF'
# AEGIS PULSE OMEGA v2 — ulimit bumps for async scraper workload.
*    soft    nofile    65536
*    hard    nofile    1048576
*    soft    nproc     32768
*    hard    nproc     65535
root soft    nofile    65536
root hard    nofile    1048576
EOF
  # Ensure PAM reads it.
  append_line_once /etc/pam.d/common-session "session required pam_limits.so"
  log_success "Installed $f (takes effect after next login / wsl --shutdown)."
}

run_step "configure_limits" configure_limits

# -----------------------------------------------------------------------------
# Detect GPU (persisted — used by 03_gpu.sh)
# -----------------------------------------------------------------------------
run_step "detect_gpu" detect_gpu

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
log_section "Phase 01 complete"
log_info "Kernel:        $(wsl_kernel_version)"
log_info "Ubuntu:        $(. /etc/os-release; echo "$VERSION")"
log_info "GPU present:   $(state_get has_gpu)"
log_info "Log file:      $AEGIS_LOG_FILE"

if [[ "$(state_get wsl_conf_needs_restart)" == "1" ]]; then
  log_warn "======================================================================"
  log_warn "  /etc/wsl.conf changed. From a Windows PowerShell, run:"
  log_warn "      wsl --shutdown"
  log_warn "  Then re-open Ubuntu and continue with 02_python.sh."
  log_warn "  (This is only needed on first install.)"
  log_warn "======================================================================"
fi

log_success "Phase 01 done."
