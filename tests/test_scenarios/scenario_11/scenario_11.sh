#!/bin/bash
# Scenario 11 — Prometheus latency spike: sustained slow requests to alert-api
# Expected: PrometheusMetricsMonitor detects high_http_latency event,
#           agent confirms p99, checks replicas and Loki logs,
#           proposes replicaCount increase or HPA.

set -euo pipefail
export $(grep -v '^#' .env | xargs)

TARGET_URL=${TARGET_URL:-"https://iot-agent-alert-api-dev.iotag-dev.com/actuator/health"}
CONCURRENCY=${CONCURRENCY:-30}
DURATION_S=${DURATION_S:-90}
REQUEST_DELAY=${REQUEST_DELAY:-0.1}  # 100ms between requests per worker → ~300 req/s total

echo "[scenario_11] Starting latency-spike simulation:"
echo "  target:      $TARGET_URL"
echo "  concurrency: $CONCURRENCY workers"
echo "  duration:    ${DURATION_S}s"

end_time=$(( $(date +%s) + DURATION_S ))

worker() {
  while [ "$(date +%s)" -lt "$end_time" ]; do
    # Use 5s timeout to accumulate in-flight requests and drive up latency
    curl -sk --max-time 5 -o /dev/null "$TARGET_URL" || true
    sleep "$REQUEST_DELAY"
  done
}

export -f worker
export end_time TARGET_URL REQUEST_DELAY

for i in $(seq 1 "$CONCURRENCY"); do
  worker &
done

echo "[scenario_11] $CONCURRENCY workers started. Waiting ${DURATION_S}s..."
wait
echo "[scenario_11] Done. Check /alerts for high_http_latency events from Prometheus monitor."

