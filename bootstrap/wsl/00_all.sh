#!/usr/bin/env bash
# =============================================================================
# AEGIS PULSE OMEGA v2 — 00_all.sh
# -----------------------------------------------------------------------------
# One-shot bootstrap orchestrator. Runs 01→05 in sequence with:
#   - Resume-on-failure (each phase is idempotent, skips what's done)
#   - Total timing
#   - Final 'aegis doctor' verification
#
# Usage:
#   bash bootstrap/wsl/00_all.sh                 # default
#   bash bootstrap/wsl/00_all.sh --cpu-only      # skip CUDA
#   bash bootstrap/wsl/00_all.sh --enable-ollama # also install Ollama + models
#   AEGIS_NONINTERACTIVE=1 bash bootstrap/wsl/00_all.sh   # CI / unattended
#   AEGIS_FORCE=1 bash bootstrap/wsl/00_all.sh            # rerun from scratch
# =============================================================================

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
# shellcheck source=./lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

aegis_enable_errtrap
require_wsl
require_not_root

# Forward all flags to phase scripts (03 + 04 parse them).
FORWARDED_FLAGS=("$@")

log_section "AEGIS PULSE OMEGA v2 — Full WSL Bootstrap"
log_info "Started:  $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
log_info "User:     $(whoami)"
log_info "Distro:   $(. /etc/os-release; echo "$PRETTY_NAME")"
log_info "Kernel:   $(uname -r)"
log_info "Repo:     $(cd -- "$SCRIPT_DIR/../.." &>/dev/null && pwd)"
log_info "Log file: $AEGIS_LOG_FILE"
log_info "Flags:    ${FORWARDED_FLAGS[*]:-<none>}"

declare -a PHASES=(
  "01_system.sh"
  "02_python.sh"
  "03_gpu.sh"
  "04_services.sh"
  "05_repo.sh"
)

TOTAL_START=$(date +%s)

for phase in "${PHASES[@]}"; do
  log_section "▶  $phase"
  PHASE_START=$(date +%s)

  # 03 and 04 accept flags; others ignore.
  if bash "$SCRIPT_DIR/$phase" "${FORWARDED_FLAGS[@]}"; then
    PHASE_END=$(date +%s)
    log_success "$phase finished in $((PHASE_END - PHASE_START))s"
  else
    rc=$?
    log_error "$phase failed with exit $rc"
    log_error "Fix the issue and re-run this orchestrator — completed phases are skipped automatically."
    exit "$rc"
  fi
done

TOTAL_END=$(date +%s)
log_section "ALL PHASES COMPLETE"
log_info "Total runtime: $((TOTAL_END - TOTAL_START))s"

# Final verification — run aegis doctor.
DOCTOR="$(cd -- "$SCRIPT_DIR/.." &>/dev/null && pwd)/aegis-doctor"
if [[ -x "$DOCTOR" ]]; then
  log_info "Running 'aegis-doctor' for final verification..."
  if "$DOCTOR"; then
    log_success "aegis-doctor: all checks passed."
  else
    log_warn "aegis-doctor reported issues. Review the output above."
  fi
else
  log_warn "aegis-doctor not found at $DOCTOR — skipping verification."
fi

if [[ "$(state_get wsl_conf_needs_restart)" == "1" ]]; then
  cat <<EOF

${C_YELLOW}${C_BOLD}┌──────────────────────────────────────────────────────────────────────┐
│  ONE MORE STEP: /etc/wsl.conf was updated on this run.               │
│                                                                      │
│  From a Windows PowerShell (NOT inside WSL), run:                    │
│                                                                      │
│      wsl --shutdown                                                  │
│                                                                      │
│  Then reopen Ubuntu. systemd will start, DNS will be stable, and     │
│  chrony + docker integration will behave correctly.                  │
└──────────────────────────────────────────────────────────────────────┘${C_RESET}
EOF
  state_set "wsl_conf_needs_restart" "0"
fi

log_success "Bootstrap done. Next: cd to the repo and follow docs/SETUP_FOR_NON_TECHNICAL.md → Step 6."
