#!/usr/bin/env bash
# bootstrap/security/00_all.sh — Master Phase 12 security bootstrap.
#
# Runs all security setup scripts in order. Idempotent: safe to re-run.
# Logs to ~/.aegis/bootstrap.log
#
# Usage:
#   bash bootstrap/security/00_all.sh                # full setup
#   bash bootstrap/security/00_all.sh --skip-vault   # skip Vault (CI)
#   bash bootstrap/security/00_all.sh --skip-tls     # skip TLS (headless env)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${HOME}/.aegis/bootstrap.log"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SKIP_VAULT=false
SKIP_SOPS=false
SKIP_TLS=false

for arg in "$@"; do
    case "${arg}" in
        --skip-vault) SKIP_VAULT=true ;;
        --skip-sops)  SKIP_SOPS=true ;;
        --skip-tls)   SKIP_TLS=true ;;
    esac
done

mkdir -p "${HOME}/.aegis"

log() {
    local level="$1"; shift
    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "[${ts}] [BOOTSTRAP] [${level}] $*" | tee -a "${LOG_FILE}"
}

run_step() {
    local name="$1"
    local script="$2"
    log INFO "── Step: ${name} ──"
    if bash "${script}" "${PROJECT_ROOT}"; then
        log INFO "✔ ${name} complete"
    else
        log ERROR "✘ ${name} FAILED (exit $?)"
        return 1
    fi
}

log INFO "========================================"
log INFO "AEGIS Phase 12 Security Bootstrap"
log INFO "Project root: ${PROJECT_ROOT}"
log INFO "========================================"

# Step 1: Vault dev mode
if [[ "${SKIP_VAULT}" == "false" ]]; then
    run_step "Vault dev mode" "${SCRIPT_DIR}/01_vault_dev.sh" || true  # non-fatal in CI
fi

# Step 2: SOPS + age key generation
if [[ "${SKIP_SOPS}" == "false" ]]; then
    run_step "SOPS + age" "${SCRIPT_DIR}/02_sops_age.sh" || true
fi

# Step 3: mkcert TLS certificates
if [[ "${SKIP_TLS}" == "false" ]]; then
    run_step "mkcert TLS" "${SCRIPT_DIR}/03_mkcert_tls.sh" || true
fi

# Step 4: Run security doctor to validate everything
log INFO "── Final validation: Security Doctor ──"
if bash "${SCRIPT_DIR}/../aegis-security-doctor"; then
    log INFO "✔ All security checks passed"
else
    log WARN "⚠ Some security checks failed — review output above"
    # Non-fatal: user can address issues after bootstrap
fi

log INFO "========================================"
log INFO "Phase 12 bootstrap complete!"
log INFO ""
log INFO "Next steps:"
log INFO "  1. Source Vault env: source ~/.aegis/vault.env"
log INFO "  2. Edit .env.example → .env with real secrets"
log INFO "  3. Encrypt: sops --encrypt --input-type dotenv --output-type yaml .env > .env.sops.yaml"
log INFO "  4. Run tests: make test"
log INFO "  5. Start stack: aegis up"
log INFO "========================================"
