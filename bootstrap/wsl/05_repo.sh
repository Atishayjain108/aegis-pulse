#!/usr/bin/env bash
# =============================================================================
# AEGIS PULSE OMEGA v2 — 05_repo.sh
# -----------------------------------------------------------------------------
# Repository + developer tooling:
#   - mkcert    (local TLS cert authority for https://aegis.localhost)
#   - pre-commit hooks (black, ruff, mypy, detect-secrets, gitleaks)
#   - git identity + SSH key generation (if absent)
#   - direnv allow for the repo (.envrc loaded on cd)
#   - Minimal .gitattributes + .editorconfig if missing (prevents CRLF disasters)
# =============================================================================

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
# shellcheck source=./lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

aegis_enable_errtrap
require_wsl
require_not_root

log_section "AEGIS Bootstrap — Phase 05: Repo & dev tooling"

# Ensure our path is correct in this shell.
export PATH="$HOME/.local/bin:$HOME/.pyenv/shims:$HOME/.pyenv/bin:$PATH"

# Repo root — assume this script lives at <repo>/bootstrap/wsl/05_repo.sh
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." &>/dev/null && pwd)"
log_info "Repo root: $REPO_ROOT"

if [[ "$REPO_ROOT" == /mnt/c/* || "$REPO_ROOT" == /mnt/d/* ]]; then
  log_error "Repo is on /mnt/<drive>/... — this is 10× slower than the WSL ext4 filesystem."
  log_error "Recommended: move to ~/code/aegis-pulse:"
  log_error "    cd ~/ && git clone <repo_url> code/aegis-pulse"
  log_warn  "Continuing anyway (performance will be poor)."
fi

# -----------------------------------------------------------------------------
# mkcert — install binary + local root CA
# -----------------------------------------------------------------------------
install_mkcert() {
  # Ubuntu 24.04 has no mkcert apt package. Use the official release binary.
  local want_version="v1.4.4"
  if cmd_exists mkcert && mkcert -version 2>&1 | grep -q "$want_version"; then
    log_info "mkcert $want_version already installed."
  else
    local url="https://dl.filippo.io/mkcert/${want_version}?for=linux/amd64"
    log_info "Downloading mkcert $want_version"
    retry 3 5 -- curl -fsSL "$url" -o "$HOME/.local/bin/mkcert"
    chmod +x "$HOME/.local/bin/mkcert"
  fi

  # Install local CA into the system trust store. Inside WSL this only
  # affects the Ubuntu-side trust store; Windows browsers are separate.
  # We warn rather than fail if CAROOT install errors.
  if ! mkcert -install 2>&1 | tee -a "$AEGIS_LOG_FILE" | grep -q "installed in the local CA store\|was already installed"; then
    log_warn "mkcert CA install had warnings (see log)."
  fi

  # Generate a dev cert bundle for localhost + aegis.localhost + *.aegis.localhost
  local certdir="$AEGIS_HOME/tls"
  mkdir -p "$certdir"
  if [[ ! -f "$certdir/aegis.localhost+3.pem" ]]; then
    ( cd "$certdir" && mkcert -cert-file aegis.localhost+3.pem -key-file aegis.localhost+3-key.pem \
        aegis.localhost "*.aegis.localhost" localhost 127.0.0.1 >>"$AEGIS_LOG_FILE" 2>&1 )
    log_success "Generated dev TLS bundle in $certdir"
  fi
}

run_step "install_mkcert" install_mkcert

# -----------------------------------------------------------------------------
# pre-commit — uv tool install + hook install (inside repo)
# -----------------------------------------------------------------------------
install_pre_commit() {
  # Prefer `uv tool install` (isolated venv per tool).
  if cmd_exists pre-commit && pre-commit --version >/dev/null 2>&1; then
    log_info "pre-commit already installed: $(pre-commit --version)"
  else
    retry 3 5 -- uv tool install "pre-commit==3.8.0" >/dev/null
    hash -r
    log_success "Installed pre-commit via uv tool."
  fi

  # Bootstrap hook install if we're inside a git repo.
  if [[ -d "$REPO_ROOT/.git" ]]; then
    if [[ -f "$REPO_ROOT/.pre-commit-config.yaml" ]]; then
      ( cd "$REPO_ROOT" && pre-commit install --install-hooks >/dev/null 2>&1 )
      log_success "pre-commit hooks installed into .git/hooks/"
    else
      log_warn "No .pre-commit-config.yaml at $REPO_ROOT — hooks will be installed by Phase 13."
    fi
  else
    log_info "Skipping hook install (not a git repo yet)."
  fi
}

run_step "install_pre_commit" install_pre_commit

# -----------------------------------------------------------------------------
# SOPS (secrets) — install pinned binary
# -----------------------------------------------------------------------------
install_sops() {
  local want="v3.9.1"
  if cmd_exists sops && sops --version 2>&1 | grep -q "${want#v}"; then
    log_info "sops ${want} already installed."
    return 0
  fi
  local url="https://github.com/getsops/sops/releases/download/${want}/sops-${want}.linux.amd64"
  retry 3 5 -- curl -fsSL "$url" -o "$HOME/.local/bin/sops"
  chmod +x "$HOME/.local/bin/sops"
  log_success "sops $(sops --version 2>&1 | head -1) installed."
}

run_step "install_sops" install_sops

# -----------------------------------------------------------------------------
# Git identity + SSH key
# -----------------------------------------------------------------------------
configure_git() {
  # Only set identity if missing — don't stomp on an existing config.
  local name email
  name=$(git config --global user.name || echo "")
  email=$(git config --global user.email || echo "")

  if [[ -z "$name" ]]; then
    if [[ "${AEGIS_NONINTERACTIVE:-0}" == "1" ]]; then
      git config --global user.name "AEGIS Bootstrap"
    else
      read -r -p "Git user.name (e.g. 'Jane Doe'): " name
      git config --global user.name "$name"
    fi
  fi
  if [[ -z "$email" ]]; then
    if [[ "${AEGIS_NONINTERACTIVE:-0}" == "1" ]]; then
      git config --global user.email "bootstrap@aegis.local"
    else
      read -r -p "Git user.email: " email
      git config --global user.email "$email"
    fi
  fi

  # Core hygiene — always safe to set.
  git config --global init.defaultBranch main
  git config --global pull.rebase true
  git config --global push.autoSetupRemote true
  git config --global fetch.prune true
  git config --global core.autocrlf input          # never convert to CRLF inside WSL
  git config --global core.eol lf
  git config --global core.fileMode false           # WSL /mnt/c chmod lies
  git config --global diff.algorithm histogram
  git config --global rerere.enabled true
  # Signed commits — generated SSH key is used later if user opts in.
  git config --global commit.gpgsign false
  log_success "Git identity: $(git config --global user.name) <$(git config --global user.email)>"
}

run_step "configure_git" configure_git

generate_ssh_key() {
  local keyfile="$HOME/.ssh/id_ed25519"
  if [[ -f "$keyfile" ]]; then
    log_info "SSH key already exists at $keyfile"
    return 0
  fi
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  ssh-keygen -t ed25519 -C "$(git config --global user.email)" -N "" -f "$keyfile" -q
  log_success "Generated SSH key: $keyfile"
  log_info "Public key (add to GitHub → Settings → SSH keys):"
  echo "---"
  cat "$keyfile.pub"
  echo "---"
}

run_step "generate_ssh_key" generate_ssh_key

# -----------------------------------------------------------------------------
# .gitattributes + .editorconfig — CRLF/LF hygiene
# -----------------------------------------------------------------------------
write_editor_configs() {
  if [[ ! -f "$REPO_ROOT/.gitattributes" ]]; then
    cat > "$REPO_ROOT/.gitattributes" <<'EOF'
# AEGIS PULSE OMEGA v2 — enforce LF everywhere; binaries marked explicit.
* text=auto eol=lf
*.sh   text eol=lf
*.py   text eol=lf
*.yml  text eol=lf
*.yaml text eol=lf
*.md   text eol=lf
*.ps1  text eol=crlf
*.png  binary
*.jpg  binary
*.gz   binary
*.whl  binary
*.onnx binary
*.pt   binary
EOF
    log_success "Wrote .gitattributes"
  fi
  if [[ ! -f "$REPO_ROOT/.editorconfig" ]]; then
    cat > "$REPO_ROOT/.editorconfig" <<'EOF'
# AEGIS PULSE OMEGA v2
root = true
[*]
charset = utf-8
end_of_line = lf
indent_style = space
indent_size = 2
insert_final_newline = true
trim_trailing_whitespace = true
[*.py]
indent_size = 4
[*.{ps1,md}]
trim_trailing_whitespace = false
EOF
    log_success "Wrote .editorconfig"
  fi
}

run_step "write_editor_configs" write_editor_configs

# -----------------------------------------------------------------------------
# direnv allow — only if .envrc exists
# -----------------------------------------------------------------------------
direnv_allow() {
  if [[ -f "$REPO_ROOT/.envrc" ]] && cmd_exists direnv; then
    ( cd "$REPO_ROOT" && direnv allow . ) || log_warn "direnv allow failed."
    log_success "direnv allow succeeded for $REPO_ROOT"
  else
    log_debug "No .envrc found — skip direnv allow."
  fi
}

run_step "direnv_allow" direnv_allow

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
log_section "Phase 05 complete"
cmd_exists mkcert     && log_info "mkcert:      $(mkcert -version 2>&1)"
cmd_exists pre-commit && log_info "pre-commit:  $(pre-commit --version)"
cmd_exists sops       && log_info "sops:        $(sops --version 2>&1 | head -1)"
cmd_exists direnv     && log_info "direnv:      $(direnv --version)"
log_info "SSH key:     ${HOME}/.ssh/id_ed25519(.pub)"
log_success "Phase 05 done."
