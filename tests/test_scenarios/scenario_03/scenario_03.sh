#!/bin/bash
# Scenario 03 — DDoS simulation: high request-rate spike on gateway API
# Expected: PrometheusMetricsMonitor detects request_rate_spike event,
#           agent confirms via Prometheus, checks replicas and Loki errors,
#           proposes replicaCount bump or HPA in values-dev.yaml and opens a PR.

set -euo pipefail

export $(grep -v '^#' .env | xargs)

TARGET_URL=${TARGET_URL:-"https://iot-agent-gateway-api-dev.iotag-dev.com/actuator/health"}
CONCURRENCY=${CONCURRENCY:-50}
DURATION_S=${DURATION_S:-60}
REQUEST_DELAY=${REQUEST_DELAY:-0.05}   # seconds between requests per worker

echo "[scenario_03] Starting DDoS simulation:"
echo "  target:      $TARGET_URL"
echo "  concurrency: $CONCURRENCY workers"
echo "  duration:    ${DURATION_S}s"
echo "  delay:       ${REQUEST_DELAY}s per worker request"

end_time=$(( $(date +%s) + DURATION_S ))

worker() {
  while [ "$(date +%s)" -lt "$end_time" ]; do
    curl -sk --max-time 2 -o /dev/null "$TARGET_URL" || true
    sleep "$REQUEST_DELAY"
  done
}

export -f worker
export end_time TARGET_URL REQUEST_DELAY

# Launch workers in background
for i in $(seq 1 "$CONCURRENCY"); do
  worker &
done

echo "[scenario_03] $CONCURRENCY workers running. PID group: $$"
echo "[scenario_03] Waiting ${DURATION_S}s for Prometheus to detect the spike..."
wait
echo "[scenario_03] Done. Check agent /alerts endpoint for request_rate_spike events."

