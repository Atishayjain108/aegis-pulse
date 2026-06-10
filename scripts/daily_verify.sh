#!/bin/bash
set -euo pipefail

echo "=== AEGIS Pulse Daily Verification ==="
echo "$(date -u)"
echo ""

PASS=0
FAIL=0

check() {
    local name="$1"
    local cmd="$2"
    printf "[%-18s] " "$name"
    if eval "$cmd" > /tmp/aegis_check_out 2>&1; then
        echo "✅ PASS"
        PASS=$((PASS + 1))
    else
        echo "❌ FAIL"
        tail -5 /tmp/aegis_check_out | sed 's/^/    /'
        FAIL=$((FAIL + 1))
    fi
}

check "Data flow"       "uv run python scripts/validate_data_flow.py"
check "Dashboard API"   "curl -sf http://localhost:8300/api/stats"
check "Execute API"     "curl -sf http://localhost:8200/healthz"
check "Predict API"     "curl -sf http://localhost:8100/healthz"
check "DB has signals"  "docker compose exec -T postgres psql -U aegis_app -d aegis -t -c 'SELECT COUNT(*) FROM signals' | tr -d ' \n' | grep -E '^[1-9]'"
check "Redis stream"    "docker compose exec -T redis redis-cli PING | grep -q PONG"
check "Ruff clean"      "uv run ruff check src/ aegis-phase4/src/ --quiet"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
echo ""

if [ "$FAIL" -eq 0 ]; then
    echo "✅ System healthy. Run: uv run aegis daily"
else
    echo "❌ $FAIL check(s) failed. See output above."
    exit 1
fi
