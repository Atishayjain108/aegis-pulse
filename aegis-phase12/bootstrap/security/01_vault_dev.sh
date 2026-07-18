#!/usr/bin/env bash
# bootstrap/security/01_vault_dev.sh — Start HashiCorp Vault in dev mode
#
# Idempotent: safe to run multiple times.
# Logs to ~/.aegis/bootstrap.log
# Exits non-zero on uncorrectable failure.
#
# FREE-TIER PATH: Vault OSS dev mode (in-memory, auto-unsealed, single node)
# PAID PATH (OPTIONAL): Vault Enterprise with HSM auto-unseal + HA Raft

set -euo pipefail

LOG_DIR="${HOME}/.aegis"
LOG_FILE="${LOG_DIR}/bootstrap.log"
VAULT_VERSION="1.17.2"
VAULT_ADDR="http://127.0.0.1:8200"
VAULT_TOKEN="dev-root-token"
VAULT_INSTALL_DIR="/usr/local/bin"
VAULT_DATA_DIR="${HOME}/.aegis/vault"

mkdir -p "${LOG_DIR}" "${VAULT_DATA_DIR}"

log() {
    local level="$1"; shift
    local msg="$*"
    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "[${ts}] [${level}] ${msg}" | tee -a "${LOG_FILE}"
}

check_vault_installed() {
    if command -v vault &>/dev/null; then
        local ver
        ver="$(vault version 2>/dev/null | grep -oP '[\d]+\.[\d]+\.[\d]+' | head -1)"
        log INFO "Vault already installed: ${ver}"
        return 0
    fi
    return 1
}

install_vault() {
    log INFO "Installing Vault ${VAULT_VERSION}..."
    local arch
    arch="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
    local url="https://releases.hashicorp.com/vault/${VAULT_VERSION}/vault_${VAULT_VERSION}_linux_${arch}.zip"
    local tmp
    tmp="$(mktemp -d)"

    curl -fsSL "${url}" -o "${tmp}/vault.zip"
    unzip -q "${tmp}/vault.zip" -d "${tmp}"
    sudo mv "${tmp}/vault" "${VAULT_INSTALL_DIR}/vault"
    sudo chmod +x "${VAULT_INSTALL_DIR}/vault"
    rm -rf "${tmp}"

    log INFO "Vault ${VAULT_VERSION} installed at ${VAULT_INSTALL_DIR}/vault"
}

is_vault_running() {
    vault status -address="${VAULT_ADDR}" &>/dev/null
}

start_vault_dev() {
    log INFO "Starting Vault dev server on ${VAULT_ADDR}..."
    # Run in background, log output to file
    nohup vault server \
        -dev \
        -dev-root-token-id="${VAULT_TOKEN}" \
        -dev-listen-address="127.0.0.1:8200" \
        > "${VAULT_DATA_DIR}/vault.log" 2>&1 &

    local vault_pid=$!
    echo "${vault_pid}" > "${VAULT_DATA_DIR}/vault.pid"
    log INFO "Vault PID: ${vault_pid}"

    # Wait for Vault to be ready (max 15s)
    local retries=15
    while [[ ${retries} -gt 0 ]]; do
        if vault status -address="${VAULT_ADDR}" &>/dev/null; then
            log INFO "Vault is ready at ${VAULT_ADDR}"
            return 0
        fi
        sleep 1
        ((retries--))
    done

    log ERROR "Vault failed to start within 15 seconds. Check: ${VAULT_DATA_DIR}/vault.log"
    exit 1
}

configure_vault() {
    log INFO "Configuring Vault KV v2 mount and Transit engine..."
    export VAULT_ADDR VAULT_TOKEN

    # Enable KV v2 at 'secret/'
    if ! vault secrets list | grep -q "^secret/"; then
        vault secrets enable -version=2 kv 2>/dev/null || true
        log INFO "KV v2 enabled at secret/"
    else
        log INFO "KV v2 already enabled"
    fi

    # Enable Transit at 'transit/'
    if ! vault secrets list | grep -q "^transit/"; then
        vault secrets enable transit
        vault write -f transit/keys/aegis-key type=aes256-gcm96
        log INFO "Transit enabled, key 'aegis-key' created"
    else
        log INFO "Transit already enabled"
    fi

    # Write example AEGIS secrets structure
    vault kv put secret/aegis/db \
        password="aegis_app_dev_pw" \
        host="localhost" \
        port="5433" \
        name="aegis"

    vault kv put secret/aegis/redis \
        url="redis://localhost:6380/0"

    vault kv put secret/aegis/minio \
        access_key="aegis-dev-key" \
        secret_key="aegis-dev-secret-please-change"

    log INFO "Example secrets written to Vault KV"
}

write_env_exports() {
    local env_file="${HOME}/.aegis/vault.env"
    cat > "${env_file}" <<EOF
# Source this file to set Vault environment variables:
#   source ~/.aegis/vault.env
export VAULT_ADDR="${VAULT_ADDR}"
export VAULT_TOKEN="${VAULT_TOKEN}"
export AEGIS_SEC_VAULT_ADDR="${VAULT_ADDR}"
export AEGIS_SEC_VAULT_TOKEN="${VAULT_TOKEN}"
export AEGIS_SEC_VAULT_DEV_MODE="true"
EOF
    log INFO "Vault env exports written to ${env_file}"
    log INFO "Run: source ${env_file}"
}

# ── Main ──────────────────────────────────────────────────────────────────── #
main() {
    log INFO "=== AEGIS Phase 12: Vault Bootstrap ==="

    if ! check_vault_installed; then
        install_vault
    fi

    if is_vault_running; then
        log INFO "Vault already running at ${VAULT_ADDR}"
    else
        start_vault_dev
    fi

    configure_vault
    write_env_exports

    log INFO "=== Vault bootstrap complete ==="
    log INFO "UI: ${VAULT_ADDR}/ui  (token: ${VAULT_TOKEN})"
}

main "$@"
