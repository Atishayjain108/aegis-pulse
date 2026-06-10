#!/usr/bin/env bash
# bootstrap/security/02_sops_age.sh — Install SOPS + age, generate key pair
#
# Idempotent: skips steps already completed.
# Logs to ~/.aegis/bootstrap.log

set -euo pipefail

LOG_FILE="${HOME}/.aegis/bootstrap.log"
SOPS_VERSION="3.9.0"
AGE_VERSION="1.2.0"
AGE_KEY_DIR="${HOME}/.config/sops/age"
AGE_KEY_FILE="${AGE_KEY_DIR}/keys.txt"
INSTALL_DIR="/usr/local/bin"

log() {
    local level="$1"; shift
    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "[${ts}] [${level}] $*" | tee -a "${LOG_FILE}"
}

install_age() {
    if command -v age &>/dev/null && command -v age-keygen &>/dev/null; then
        log INFO "age already installed: $(age --version 2>/dev/null | head -1)"
        return 0
    fi

    log INFO "Installing age ${AGE_VERSION}..."
    local arch
    arch="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
    local url="https://github.com/FiloSottile/age/releases/download/v${AGE_VERSION}/age-v${AGE_VERSION}-linux-${arch}.tar.gz"
    local tmp
    tmp="$(mktemp -d)"

    curl -fsSL "${url}" -o "${tmp}/age.tar.gz"
    tar -xzf "${tmp}/age.tar.gz" -C "${tmp}"
    sudo mv "${tmp}/age/age" "${INSTALL_DIR}/age"
    sudo mv "${tmp}/age/age-keygen" "${INSTALL_DIR}/age-keygen"
    sudo chmod +x "${INSTALL_DIR}/age" "${INSTALL_DIR}/age-keygen"
    rm -rf "${tmp}"

    log INFO "age ${AGE_VERSION} installed"
}

install_sops() {
    if command -v sops &>/dev/null; then
        log INFO "sops already installed: $(sops --version 2>/dev/null | head -1)"
        return 0
    fi

    log INFO "Installing SOPS ${SOPS_VERSION}..."
    local arch
    arch="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
    local url="https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.${arch}"

    sudo curl -fsSL "${url}" -o "${INSTALL_DIR}/sops"
    sudo chmod +x "${INSTALL_DIR}/sops"

    log INFO "SOPS ${SOPS_VERSION} installed"
}

generate_age_key() {
    if [[ -f "${AGE_KEY_FILE}" ]]; then
        log INFO "Age key already exists at ${AGE_KEY_FILE}"
        local pubkey
        pubkey="$(grep 'public key:' "${AGE_KEY_FILE}" | awk '{print $NF}')"
        log INFO "Public key: ${pubkey}"
        return 0
    fi

    log INFO "Generating age key pair..."
    mkdir -p "${AGE_KEY_DIR}"
    chmod 700 "${AGE_KEY_DIR}"
    age-keygen -o "${AGE_KEY_FILE}" 2>&1 | tee -a "${LOG_FILE}"
    chmod 600 "${AGE_KEY_FILE}"

    local pubkey
    pubkey="$(grep 'public key:' "${AGE_KEY_FILE}" | awk '{print $NF}')"
    log INFO "Age key generated. Public key: ${pubkey}"
    echo "${pubkey}"
}

write_sops_config() {
    local project_root="${1:-.}"
    local sops_config="${project_root}/.sops.yaml"

    if [[ -f "${sops_config}" ]]; then
        log INFO ".sops.yaml already exists: ${sops_config}"
        return 0
    fi

    local pubkey
    pubkey="$(grep 'public key:' "${AGE_KEY_FILE}" | awk '{print $NF}')"

    cat > "${sops_config}" <<EOF
# .sops.yaml — AEGIS Pulse SOPS configuration
# Commit this file to git; NEVER commit .env or keys.txt
creation_rules:
  - path_regex: \.env\.sops\.yaml$
    age: >-
      ${pubkey}
  - path_regex: secrets/.*\.yaml$
    age: >-
      ${pubkey}
EOF

    log INFO ".sops.yaml written at ${sops_config}"
}

create_example_env() {
    local project_root="${1:-.}"
    local env_example="${project_root}/.env.example"
    local env_sops="${project_root}/.env.sops.yaml"

    if [[ -f "${env_sops}" ]]; then
        log INFO ".env.sops.yaml already exists"
        return 0
    fi

    # Create minimal example .env
    cat > "${project_root}/.env.example.plaintext" <<EOF
# AEGIS Pulse — Example secrets (NEVER commit .env with real values)
# Encrypt with: sops --encrypt --input-type dotenv --output-type yaml .env > .env.sops.yaml

AEGIS_SEC_JWT_SECRET=CHANGE-ME-generate-with-openssl-rand-hex-32
AEGIS_SEC_HMAC_KEY=CHANGE-ME-generate-with-openssl-rand-hex-32
AEGIS_SEC_VAULT_TOKEN=dev-root-token

# Phase 0-4 secrets
AEGIS_PG_DSN=postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis
AEGIS_REDIS_URL=redis://localhost:6380/0
AEGIS_MINIO_ACCESS_KEY=aegis-dev-key
AEGIS_MINIO_SECRET_KEY=aegis-dev-secret-please-change

# Optional: LLM API keys (leave blank to use local Ollama)
AEGIS_GROQ_API_KEY=
AEGIS_OPENROUTER_API_KEY=
AEGIS_GEMINI_API_KEY=
EOF

    log INFO "Example .env template created: ${project_root}/.env.example.plaintext"
    log INFO "Edit it with real values, then run:"
    log INFO "  sops --encrypt --input-type dotenv --output-type yaml .env.example.plaintext > .env.sops.yaml"
}

main() {
    local project_root="${1:-.}"
    log INFO "=== AEGIS Phase 12: SOPS + age Bootstrap ==="

    install_age
    install_sops
    generate_age_key
    write_sops_config "${project_root}"
    create_example_env "${project_root}"

    log INFO "=== SOPS + age bootstrap complete ==="
    log INFO "Next steps:"
    log INFO "  1. Edit .env.example.plaintext with real secrets"
    log INFO "  2. Run: sops --encrypt --input-type dotenv --output-type yaml .env.example.plaintext > .env.sops.yaml"
    log INFO "  3. Commit .env.sops.yaml and .sops.yaml to git"
    log INFO "  4. NEVER commit .env.example.plaintext or keys.txt"
}

main "$@"
