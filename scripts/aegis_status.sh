#!/bin/bash
# AEGIS Pulse — Master status check
# Run: bash scripts/aegis_status.sh

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║         AEGIS PULSE — SYSTEM STATUS                 ║"
printf "║  %s UTC  ║\n" "$(date -u '+%Y-%m-%d %H:%M:%S')"
echo "╠══════════════════════════════════════════════════════╣"

# Services
echo "║ SERVICES:                                            ║"
for svc in aegis-postgres aegis-redis aegis-execute-api \
           aegis-execute-drain aegis-dashboard aegis-predict; do
    running=$(docker inspect --format='{{.State.Running}}' "$svc" 2>/dev/null)
    if [ "$running" = "true" ]; then
        printf "║   ✅ %-46s ║\n" "$svc"
    else
        printf "║   ❌ %-46s ║\n" "$svc  ← NOT RUNNING"
    fi
done

# Data
SIGNALS=$(docker compose exec -T postgres \
  psql -U aegis_app -d aegis -t \
  -c "SELECT COUNT(*) FROM signals" 2>/dev/null | tr -d ' \n')

FRESH=$(docker compose exec -T postgres \
  psql -U aegis_app -d aegis -t \
  -c "SELECT COUNT(*) FROM signals WHERE created_at > NOW()-INTERVAL '1 hour'" \
  2>/dev/null | tr -d ' \n')

ALERTS=$(docker compose exec -T postgres \
  psql -U aegis_app -d aegis -t \
  -c "SELECT COUNT(*) FROM alerts" 2>/dev/null | tr -d ' \n')

OUTCOMES=$(docker compose exec -T postgres \
  psql -U aegis_app -d aegis -t \
  -c "SELECT COUNT(*) FROM prediction_outcomes" 2>/dev/null | tr -d ' \n')

STREAM=$(docker compose exec -T redis \
  redis-cli -p 6379 XLEN aegis:phase2:graph_results 2>/dev/null)

echo "╠══════════════════════════════════════════════════════╣"
printf "║ DATA:                                                ║\n"
printf "║   Total signals:    %-32s ║\n" "${SIGNALS:-ERROR}"
printf "║   Signals (1h):     %-32s ║\n" "${FRESH:-ERROR}"
printf "║   Total alerts:     %-32s ║\n" "${ALERTS:-ERROR}"
printf "║   Trade outcomes:   %-32s ║\n" "${OUTCOMES:-ERROR}"
printf "║   Stream entries:   %-32s ║\n" "${STREAM:-ERROR}"

# API health
DASH=$(curl -s -o /dev/null -w "%{http_code}" \
  --max-time 3 http://localhost:8300/healthz 2>/dev/null)
EXEC=$(curl -s -o /dev/null -w "%{http_code}" \
  --max-time 3 http://localhost:8200/healthz 2>/dev/null)
PRED=$(curl -s -o /dev/null -w "%{http_code}" \
  --max-time 3 http://localhost:8100/healthz 2>/dev/null)

echo "╠══════════════════════════════════════════════════════╣"
printf "║ APIS:                                                ║\n"
printf "║   Dashboard  :8300  HTTP %-27s ║\n" "${DASH:-ERR}"
printf "║   Execute    :8200  HTTP %-27s ║\n" "${EXEC:-ERR}"
printf "║   Predict    :8100  HTTP %-27s ║\n" "${PRED:-ERR}"
echo "╠══════════════════════════════════════════════════════╣"
echo "║ WEB UIs:                                             ║"
echo "║   Dashboard:    http://localhost:8300                ║"
echo "║   Grafana:      http://localhost:3001                ║"
echo "║   Prometheus:   http://localhost:9091                ║"
echo "║   Jaeger:       http://localhost:16687               ║"
echo "║   MinIO:        http://localhost:9003                ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║ DAILY COMMANDS:                                      ║"
echo "║  uv run aegis daily                                  ║"
echo "║  uv run python scripts/validate_data_flow.py         ║"
echo "║  bash scripts/aegis_status.sh                        ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
