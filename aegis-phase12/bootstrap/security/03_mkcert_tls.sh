#!/usr/bin/env bash
# bootstrap/security/03_mkcert_tls.sh — Generate locally-trusted TLS certs
#
# Installs mkcert, registers its CA with the system trust store,
# and generates certificates for all AEGIS local services.
#
# Idempotent: skips if certs already exist and are not near expiry.

set -euo pipefail

LOG_FILE="${HOME}/.aegis/bootstrap.log"
MKCERT_VERSION="1.4.4"
INSTALL_DIR="/usr/local/bin"
CERTS_DIR="${HOME}/code/aegis-pulse/certs"

log() {
    local level="$1"; shift
    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "[${ts}] [${level}] $*" | tee -a "${LOG_FILE}"
}

install_mkcert() {
    if command -v mkcert &>/dev/null; then
        log INFO "mkcert already installed: $(mkcert --version 2>/dev/null)"
        return 0
    fi

    log INFO "Installing mkcert ${MKCERT_VERSION}..."
    local arch
    arch="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
    local url="https://github.com/FiloSottile/mkcert/releases/download/v${MKCERT_VERSION}/mkcert-v${MKCERT_VERSION}-linux-${arch}"

    sudo curl -fsSL "${url}" -o "${INSTALL_DIR}/mkcert"
    sudo chmod +x "${INSTALL_DIR}/mkcert"
    log INFO "mkcert ${MKCERT_VERSION} installed"
}

install_ca() {
    log INFO "Installing mkcert CA in system trust store..."
    # This requires nss (for Chrome/Firefox) and ca-certificates
    sudo apt-get install -y -qq libnss3-tools ca-certificates 2>/dev/null || true
    mkcert -install
    log INFO "mkcert CA installed"
}

generate_certs() {
    mkdir -p "${CERTS_DIR}"

    local domains=(
        "localhost"
        "127.0.0.1"
        "::1"
        "aegis-predict"
        "aegis-execute"
        "aegis-dashboard"
        "aegis-vault"
    )

    local cert_file="${CERTS_DIR}/server.crt"
    local key_file="${CERTS_DIR}/server.key"

    if [[ -f "${cert_file}" ]]; then
        # Check expiry (skip if > 30 days remaining)
        local days_left
        days_left="$(openssl x509 -in "${cert_file}" -noout -checkend $((30 * 86400)) &>/dev/null && echo "ok" || echo "expiring")"
        if [[ "${days_left}" == "ok" ]]; then
            log INFO "TLS cert exists and is valid for >30 days: ${cert_file}"
            return 0
        fi
        log INFO "TLS cert near expiry, regenerating..."
    fi

    log INFO "Generating TLS certs for: ${domains[*]}"
    mkcert \
        -cert-file "${cert_file}" \
        -key-file "${key_file}" \
        "${domains[@]}"

    chmod 644 "${cert_file}"
    chmod 600 "${key_file}"
    log INFO "Certs written: ${cert_file}, ${key_file}"
}

write_cert_env() {
    local env_file="${HOME}/.aegis/tls.env"
    cat > "${env_file}" <<EOF
# Source this to configure AEGIS services to use local TLS certs
export AEGIS_SEC_TLS_CERT_PATH="${CERTS_DIR}/server.crt"
export AEGIS_SEC_TLS_KEY_PATH="${CERTS_DIR}/server.key"
EOF
    log INFO "TLS env written: ${env_file}"
}

main() {
    log INFO "=== AEGIS Phase 12: mkcert TLS Bootstrap ==="
    install_mkcert
    install_ca
    generate_certs
    write_cert_env
    log INFO "=== TLS bootstrap complete ==="
    log INFO "Cert: ${CERTS_DIR}/server.crt"
    log INFO "Key:  ${CERTS_DIR}/server.key"
}

main "$@"
