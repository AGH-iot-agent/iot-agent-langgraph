#!/bin/bash
# Scenario 14 — PVC disk pressure simulation in namespace iotag-dev
# Expected: DiskPressureMonitor detects pvc_disk_pressure event via Prometheus
#           (kubelet_volume_stats metrics), agent fetches PVC info, identifies
#           the service consuming space, proposes PVC expansion + log rotation,
#           creates a GitHub Issue with the fix proposal.
#
# Mechanism:
#   1. Find a pod with a writable PVC mount in the namespace.
#   2. Use kubectl exec + dd to write large dummy files until PVC > 80%.
#   3. Monitor until agent fires pvc_disk_pressure event.
#   4. Cleanup: delete dummy files from pod.
#
# SAFETY: Only writes to /tmp inside the pod by default.
#         Override WRITE_PATH to a PVC mount path with care.
#
# Prerequisites: kubectl access to iotag-dev, GITHUB_TOKEN in .env

set -euo pipefail
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

NAMESPACE=${NAMESPACE:-iotag-dev}
TARGET_POD=${TARGET_POD:-}
WRITE_PATH=${WRITE_PATH:-/tmp/scenario_14_fill}
FILL_SIZE_MB=${FILL_SIZE_MB:-500}
AGENT_URL=${AGENT_URL:-http://10.43.10.43}

echo "[scenario_14] ===== PVC Disk Pressure Test ====="
echo "  namespace:   $NAMESPACE"
echo "  fill size:   ${FILL_SIZE_MB}MB"
echo "  write path:  $WRITE_PATH"

# --- Step 1: Find a running pod to write into ---
if [ -z "$TARGET_POD" ]; then
  CANDIDATE_PODS=$(kubectl get pods -n "$NAMESPACE" \
    --field-selector=status.phase=Running \
    --no-headers -o custom-columns=NAME:.metadata.name \
    || true)

  while IFS= read -r candidate; do
    [ -z "$candidate" ] && continue
    if kubectl exec -n "$NAMESPACE" "$candidate" -- /bin/sh -c "echo ok" >/dev/null 2>&1; then
      TARGET_POD="$candidate"
      break
    fi
    if kubectl exec -n "$NAMESPACE" "$candidate" -- mkdir -p /tmp >/dev/null 2>&1; then
      TARGET_POD="$candidate"
      break
    fi
  done <<< "$CANDIDATE_PODS"
fi

if [ -z "$TARGET_POD" ]; then
  echo "[scenario_14] ERROR: No running pod found in namespace $NAMESPACE"
  exit 1
fi
echo "[scenario_14] Target pod: $TARGET_POD"

# --- Step 2: Create write directory inside pod ---
echo "[scenario_14] Creating fill directory inside pod..."
kubectl exec -n "$NAMESPACE" "$TARGET_POD" -- mkdir -p "$WRITE_PATH" || {
  echo "[scenario_14] mkdir failed — pod may not have shell. Trying /bin/sh..."
  kubectl exec -n "$NAMESPACE" "$TARGET_POD" -- /bin/sh -c "mkdir -p $WRITE_PATH"
}

# --- Step 3: Write large dummy files ---
echo "[scenario_14] Writing ${FILL_SIZE_MB}MB to $WRITE_PATH in pod $TARGET_POD..."
echo "[scenario_14] This will trigger PVC usage spike in kubelet_volume_stats metrics."

CHUNK_MB=50
CHUNKS=$(( FILL_SIZE_MB / CHUNK_MB ))

for i in $(seq 1 "$CHUNKS"); do
  echo "[scenario_14] Writing chunk $i/$CHUNKS (${CHUNK_MB}MB)..."
  kubectl exec -n "$NAMESPACE" "$TARGET_POD" -- \
    dd if=/dev/zero of="${WRITE_PATH}/fill_${i}.bin" bs=1M count="$CHUNK_MB" 2>/dev/null || {
      echo "[scenario_14] dd failed for chunk $i — trying with /bin/sh -c..."
      kubectl exec -n "$NAMESPACE" "$TARGET_POD" -- \
        /bin/sh -c "dd if=/dev/zero of=${WRITE_PATH}/fill_${i}.bin bs=1M count=${CHUNK_MB}"
    }
  sleep 2
done

echo "[scenario_14] Fill complete. Waiting 60s for kubelet to update volume stats..."
echo "[scenario_14] (kubelet_volume_stats metrics update every ~60s)"
sleep 60

# --- Step 4: Check disk usage from inside pod ---
echo "[scenario_14] Disk usage in $WRITE_PATH:"
kubectl exec -n "$NAMESPACE" "$TARGET_POD" -- \
  df -h "$WRITE_PATH" 2>/dev/null || \
  kubectl exec -n "$NAMESPACE" "$TARGET_POD" -- /bin/sh -c "df -h $WRITE_PATH" || true

# --- Step 5: Verify via Prometheus (if accessible) ---
PROMETHEUS_URL=${PROMETHEUS_URL:-http://10.43.253.229:9090}
echo "[scenario_14] Checking Prometheus PVC metrics (if accessible):"
curl -s "${PROMETHEUS_URL}/api/v1/query" \
  --data-urlencode "query=kubelet_volume_stats_used_bytes{namespace=\"$NAMESPACE\"}" \
  2>/dev/null | python3 -c "
import json,sys
try:
  data=json.load(sys.stdin)
  for r in data.get('data',{}).get('result',[]):
    pvc=r['metric'].get('persistentvolumeclaim','?')
    val=float(r['value'][1])
    print(f'  PVC {pvc}: {val/1024/1024:.1f} MB used')
except: print('  (could not parse Prometheus response)')
" 2>/dev/null || echo "  (Prometheus not accessible at $PROMETHEUS_URL)"

echo ""
echo "[scenario_14] ===== Scenario active — agent should detect pvc_disk_pressure ====="
echo "[scenario_14] Monitor agent at: $AGENT_URL/watch/monitors"
echo "[scenario_14] Check recent alerts: $AGENT_URL/alerts"
echo ""
echo "[scenario_14] Cleanup: run cleanup_14.sh to remove dummy files."

# Save state for cleanup
cat > .scenario_14_state <<EOF
NAMESPACE=$NAMESPACE
TARGET_POD=$TARGET_POD
WRITE_PATH=$WRITE_PATH
EOF
echo "[scenario_14] State saved to .scenario_14_state"
