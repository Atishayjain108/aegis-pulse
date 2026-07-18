#!/usr/bin/env bash
# Shared entrypoint for the API and drainer images.
#
# Usage: entrypoint.sh <command>
#   api    — run uvicorn against aegis.execute.api.app:build_app
#   drain  — run `aegis-execute drain` against the configured tenant
#   shell  — drop into a bash shell (debugging)
#
# Reads env vars:
#   AEGIS_EXECUTE_API_HOST          (default: 0.0.0.0)
#   AEGIS_EXECUTE_API_PORT          (default: 8200)
#   AEGIS_PG_DSN                    (optional)
#   AEGIS_REDIS_URL                 (optional)
#   AEGIS_EXECUTE_TENANT_ID         (required for `drain`)
#   AEGIS_EXECUTE_WAIT_FOR_DEPS_S   (default: 0, set to >0 to block)
#
# All output goes to stdout/stderr — no log files.

set -euo pipefail

CMD="${1:-api}"
HOST="${AEGIS_EXECUTE_API_HOST:-0.0.0.0}"
PORT="${AEGIS_EXECUTE_API_PORT:-8200}"
WAIT_S="${AEGIS_EXECUTE_WAIT_FOR_DEPS_S:-0}"

log() { printf '[entrypoint] %s\n' "$*" >&2; }

wait_for_tcp() {
    # $1 = host:port  $2 = timeout seconds
    local addr="$1"
    local timeout="$2"
    local end=$((SECONDS + timeout))
    local host="${addr%%:*}"
    local port="${addr##*:}"
    log "waiting up to ${timeout}s for ${host}:${port}..."
    while (( SECONDS < end )); do
        if (echo > "/dev/tcp/${host}/${port}") 2>/dev/null; then
            log "${host}:${port} is reachable"
            return 0
        fi
        sleep 1
    done
    log "WARNING: ${host}:${port} unreachable after ${timeout}s — continuing anyway"
    return 1
}

# Optional dependency wait — useful in docker-compose where services
# come up in parallel.
if (( WAIT_S > 0 )); then
    if [[ -n "${AEGIS_PG_DSN:-}" ]]; then
        # Extract host:port from "postgresql://user:pw@host:5432/db"
        pg_hostport=$(echo "${AEGIS_PG_DSN}" | sed -E 's|^[a-z]+://[^@]+@([^/]+).*|\1|')
        wait_for_tcp "${pg_hostport}" "${WAIT_S}" || true
    fi
    if [[ -n "${AEGIS_REDIS_URL:-}" ]]; then
        redis_hostport=$(echo "${AEGIS_REDIS_URL}" | sed -E 's|^redis://([^/]+).*|\1|')
        wait_for_tcp "${redis_hostport}" "${WAIT_S}" || true
    fi
fi

case "${CMD}" in
    api)
        log "starting API on ${HOST}:${PORT}"
        # We invoke uvicorn directly so we keep control over reload/workers.
        exec python -m uvicorn \
            "aegis.execute.api.app:build_app" \
            --factory \
            --host "${HOST}" \
            --port "${PORT}" \
            --no-access-log
        ;;
    drain)
        : "${AEGIS_EXECUTE_TENANT_ID:?AEGIS_EXECUTE_TENANT_ID env var is required for drain}"
        log "starting drainer for tenant ${AEGIS_EXECUTE_TENANT_ID}"
        args=(drain --tenant "${AEGIS_EXECUTE_TENANT_ID}")
        if [[ -n "${AEGIS_PG_DSN:-}" ]]; then
            args+=(--pg-dsn "${AEGIS_PG_DSN}")
        fi
        if [[ -n "${AEGIS_REDIS_URL:-}" ]]; then
            args+=(--redis-url "${AEGIS_REDIS_URL}")
        fi
        exec aegis-execute "${args[@]}"
        ;;
    shell)
        exec /bin/bash
        ;;
    *)
        # Unknown command — pass through to allow `docker run image bash -c '...'`
        log "running passthrough: $*"
        exec "$@"
        ;;
esac
