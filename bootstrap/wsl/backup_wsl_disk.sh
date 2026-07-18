#!/usr/bin/env bash
# backup_wsl_disk.sh
#
# Create an encrypted snapshot of the WSL2 Ubuntu distro for disaster recovery.
# Must be run from a Windows PowerShell or WSL2 terminal that has access to
# the Windows filesystem.
#
# Usage (from PowerShell):
#   wsl -e bash /path/to/backup_wsl_disk.sh [backup_dir] [max_snapshots]
#
# Usage (from WSL2):
#   ./backup_wsl_disk.sh /mnt/d/Backups/WSL 5
#
# Requirements:
#   - wsl.exe available on PATH (Windows build ≥ 1903)
#   - At least 20 GB free on the backup target drive
#   - age or gpg for encryption (optional but recommended)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BACKUP_DIR="${1:-/mnt/d/Backups/WSL}"
MAX_SNAPSHOTS="${2:-5}"
DISTRO_NAME="${WSL_DISTRO_NAME:-Ubuntu-24.04}"
ENCRYPT="${BACKUP_ENCRYPT:-false}"   # set to "true" to encrypt with age
AGE_RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"
}

die() {
    log "ERROR: $*" >&2
    exit 1
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "'$1' not found on PATH"
}

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

require_cmd wsl.exe

# Verify the distro is registered.
if ! wsl.exe --list --quiet 2>/dev/null | grep -qi "$DISTRO_NAME"; then
    die "WSL distro '$DISTRO_NAME' not found. List distros with: wsl.exe --list"
fi

# Verify backup directory is writable.
mkdir -p "$BACKUP_DIR" || die "Cannot create backup directory: $BACKUP_DIR"
touch "$BACKUP_DIR/.probe" && rm "$BACKUP_DIR/.probe" \
    || die "Backup directory is not writable: $BACKUP_DIR"

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
RAW_FILE="$BACKUP_DIR/wsl-${DISTRO_NAME}-${TIMESTAMP}.tar"

log "Exporting WSL2 distro: $DISTRO_NAME"
log "Destination: $RAW_FILE"
log "This may take 5–15 minutes depending on distro size…"

wsl.exe --export "$DISTRO_NAME" "$RAW_FILE" \
    || die "wsl --export failed with exit code $?"

# ---------------------------------------------------------------------------
# Compress
# ---------------------------------------------------------------------------

log "Compressing with gzip…"
gzip --fast "$RAW_FILE"
FINAL_FILE="${RAW_FILE}.gz"

SIZE_MB="$(du -m "$FINAL_FILE" | cut -f1)"
log "Compressed snapshot: ${SIZE_MB} MB → $FINAL_FILE"

# ---------------------------------------------------------------------------
# Encrypt (optional)
# ---------------------------------------------------------------------------

if [[ "$ENCRYPT" == "true" ]]; then
    require_cmd age
    [[ -n "$AGE_RECIPIENT" ]] || die "Set BACKUP_AGE_RECIPIENT to the age public key"
    log "Encrypting with age…"
    age -r "$AGE_RECIPIENT" -o "${FINAL_FILE}.age" "$FINAL_FILE"
    rm "$FINAL_FILE"
    FINAL_FILE="${FINAL_FILE}.age"
    log "Encrypted snapshot: $FINAL_FILE"
fi

# ---------------------------------------------------------------------------
# Checksum
# ---------------------------------------------------------------------------

log "Computing SHA-256 checksum…"
sha256sum "$FINAL_FILE" > "${FINAL_FILE}.sha256"
log "Checksum: $(cat "${FINAL_FILE}.sha256")"

# ---------------------------------------------------------------------------
# Prune old snapshots
# ---------------------------------------------------------------------------

log "Pruning old snapshots (keeping last $MAX_SNAPSHOTS)…"

# List all snapshots for this distro, sorted by name (oldest first).
mapfile -t old_files < <(
    ls -t "${BACKUP_DIR}/wsl-${DISTRO_NAME}-"*.tar.gz{,.age} 2>/dev/null \
        | tail -n +"$((MAX_SNAPSHOTS + 1))"
)

if [[ ${#old_files[@]} -gt 0 ]]; then
    for f in "${old_files[@]}"; do
        log "Removing old snapshot: $f"
        rm -f "$f" "${f}.sha256"
    done
    log "Pruned ${#old_files[@]} old snapshot(s)."
else
    log "No old snapshots to prune."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

log "====================================="
log "WSL2 backup complete."
log "  Distro  : $DISTRO_NAME"
log "  File    : $FINAL_FILE"
log "  Size    : ${SIZE_MB} MB"
log "  Time    : $TIMESTAMP"
log "====================================="
log "To restore on a new machine:"
log "  wsl.exe --import $DISTRO_NAME C:\\WSL $FINAL_FILE"
