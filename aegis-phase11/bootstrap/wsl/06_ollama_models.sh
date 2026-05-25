#!/usr/bin/env bash
# =============================================================================
# bootstrap/wsl/06_ollama_models.sh — Pull required Ollama models (idempotent)
# =============================================================================
#
# Phase 11 — Local LLM Orchestration
# Pulls the AEGIS model zoo into Ollama.  Skips models already present.
#
# Usage:
#   bash bootstrap/wsl/06_ollama_models.sh [--cpu-only] [--minimal]
#
# Options:
#   --cpu-only   Only pull CPU-viable models (skips 14B+ unless you have 16GB RAM)
#   --minimal    Pull only the minimum required model (llama3.2:3b)
#
# Environment:
#   OLLAMA_BASE_URL  — default: http://localhost:11434
#   AEGIS_LOG        — path to bootstrap log (default: ~/.aegis/bootstrap.log)
# =============================================================================

set -euo pipefail

OLLAMA_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
LOG="${AEGIS_LOG:-$HOME/.aegis/bootstrap.log}"
CPU_ONLY=false
MINIMAL=false

mkdir -p "$(dirname "$LOG")"

log() {
    local ts
    ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "[$ts] $*" | tee -a "$LOG"
}

err() {
    log "ERROR: $*" >&2
}

# Parse flags
for arg in "$@"; do
    case "$arg" in
        --cpu-only) CPU_ONLY=true ;;
        --minimal)  MINIMAL=true ;;
    esac
done

# ---------------------------------------------------------------------------
# Check Ollama is running
# ---------------------------------------------------------------------------

log "Checking Ollama at $OLLAMA_URL ..."
if ! curl -sf "$OLLAMA_URL/api/version" > /dev/null 2>&1; then
    log "Ollama not running. Starting Ollama in background ..."
    ollama serve &>/tmp/ollama.log &
    OLLAMA_PID=$!
    # Wait up to 30 s for Ollama to start
    for i in $(seq 1 30); do
        if curl -sf "$OLLAMA_URL/api/version" > /dev/null 2>&1; then
            log "Ollama started (PID $OLLAMA_PID)"
            break
        fi
        sleep 1
    done
    if ! curl -sf "$OLLAMA_URL/api/version" > /dev/null 2>&1; then
        err "Ollama failed to start within 30s. Check /tmp/ollama.log"
        exit 1
    fi
fi

log "Ollama is reachable."

# ---------------------------------------------------------------------------
# Helper: pull model only if not already present
# ---------------------------------------------------------------------------

pull_if_missing() {
    local model="$1"
    log "Checking model: $model"
    # List models and grep for exact name
    if ollama list 2>/dev/null | awk '{print $1}' | grep -qxF "$model"; then
        log "  ✓ Already present: $model"
        return 0
    fi
    log "  ↓ Pulling: $model (this may take several minutes) ..."
    if ollama pull "$model"; then
        log "  ✓ Pulled: $model"
    else
        err "  ✗ Failed to pull: $model (continuing)"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Model zoo
# ---------------------------------------------------------------------------

log "=== AEGIS Phase 11 — Ollama Model Bootstrap ==="

if $MINIMAL; then
    log "[minimal] Pulling only minimum required model"
    pull_if_missing "llama3.2:3b"
    log "Done (minimal). Run without --minimal to pull the full model zoo."
    exit 0
fi

# Always pull the fast / small model (CPU-viable, ~2GB)
pull_if_missing "llama3.2:3b"

# Embedding model (required for SemanticRouter and ChromaDB memory)
pull_if_missing "bge-m3"

if ! $CPU_ONLY; then
    # Primary reasoning model (~8GB VRAM or 16GB RAM)
    pull_if_missing "qwen2.5:14b"

    # Code-specialised model (~8GB VRAM)
    pull_if_missing "qwen2.5-coder:14b"

    # Small but sharp — good CPU option when RAM > 8GB
    pull_if_missing "phi4"
else
    log "[cpu-only] Skipping 14B models (need --cpu-only unset + 16GB RAM)"
    # Phi-4 is CPU-viable at 4-bit quant (~6GB RAM)
    pull_if_missing "phi4:q4_K_M"
fi

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

log ""
log "=== Installed models ==="
ollama list 2>/dev/null | tee -a "$LOG"
log ""
log "Bootstrap complete. Run 'aegis llm health' to verify."
