#!/bin/bash
# Scenario 13 — ResourceQuota exhaustion in namespace iotag-dev
# Expected: ResourceQuotaMonitor detects namespace_quota_exceeded event,
#           agent identifies top pod consumers, proposes quota increase or
#           cleanup of Failed/Evicted pods, creates a GitHub Issue with fix.
#
# Mechanism:
#   1. Query current pod count in namespace.
#   2. Patch the ResourceQuota to set pods hard limit = current_count + 1.
#   3. Trigger a new Deployment scaled to 3 replicas — the 3rd pod will be rejected.
#   4. Agent detects quota_exceeded, diagnoses, proposes fix and restores quota.
#
# Prerequisites: kubectl access to iotag-dev, GITHUB_TOKEN in .env

set -euo pipefail

export $(grep -v '^#' .env | xargs)

NAMESPACE=${NAMESPACE:-iotag-dev}
QUOTA_NAME=${QUOTA_NAME:-default-quota}
TEST_DEPLOYMENT=${TEST_DEPLOYMENT:-iot-agent-sim-devices}
AGENT_URL=${AGENT_URL:-http://localhost:8000}

echo "[scenario_13] ===== ResourceQuota Exhaustion Test ====="
echo "  namespace:   $NAMESPACE"
echo "  quota:       $QUOTA_NAME"
echo "  deployment:  $TEST_DEPLOYMENT"

# --- Step 1: Snapshot current pod count ---
CURRENT_PODS=$(kubectl get pods -n "$NAMESPACE" --field-selector=status.phase=Running \
  --no-headers 2>/dev/null | wc -l | tr -d ' ')
echo "[scenario_13] Current running pod count: $CURRENT_PODS"

# --- Step 2: Check if ResourceQuota exists; if not, create a restrictive one ---
if ! kubectl get resourcequota "$QUOTA_NAME" -n "$NAMESPACE" &>/dev/null; then
  echo "[scenario_13] No ResourceQuota found — creating one with hard limit pods=$((CURRENT_PODS + 1))"
  kubectl apply -f - <<EOF
apiVersion: v1
kind: ResourceQuota
metadata:
  name: $QUOTA_NAME
  namespace: $NAMESPACE
spec:
  hard:
    pods: "$((CURRENT_PODS + 1))"
    requests.cpu: "8"
    requests.memory: 16Gi
    limits.cpu: "16"
    limits.memory: 32Gi
EOF
  QUOTA_CREATED=true
else
  # Patch existing quota: reduce pods limit to current + 1 (tight but not yet exhausted)
  echo "[scenario_13] Patching existing ResourceQuota pods limit to $((CURRENT_PODS + 1))"
  kubectl patch resourcequota "$QUOTA_NAME" -n "$NAMESPACE" \
    --type merge \
    -p "{\"spec\":{\"hard\":{\"pods\":\"$((CURRENT_PODS + 1))\"}}}}"
  QUOTA_CREATED=false
fi

echo "[scenario_13] Quota set. Sleeping 5s for quota to propagate..."
sleep 5

# --- Step 3: Scale up deployment to trigger quota violation ---
ORIGINAL_REPLICAS=$(kubectl get deployment "$TEST_DEPLOYMENT" -n "$NAMESPACE" \
  -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "1")
echo "[scenario_13] Original replicas for $TEST_DEPLOYMENT: $ORIGINAL_REPLICAS"
echo "[scenario_13] Scaling $TEST_DEPLOYMENT to $((ORIGINAL_REPLICAS + 3)) to exhaust quota..."

kubectl scale deployment "$TEST_DEPLOYMENT" -n "$NAMESPACE" \
  --replicas=$((ORIGINAL_REPLICAS + 3)) || true

echo "[scenario_13] Waiting 30s for quota violation event to be emitted..."
sleep 30

# --- Step 4: Verify the quota violation appears in events ---
echo "[scenario_13] Checking for quota violation events:"
kubectl get events -n "$NAMESPACE" \
  --field-selector reason=FailedCreate \
  --sort-by='.lastTimestamp' | tail -5 || echo "(no FailedCreate events yet)"

echo "[scenario_13] Current quota status:"
kubectl describe resourcequota "$QUOTA_NAME" -n "$NAMESPACE" || true

echo "[scenario_13] ===== Scenario active — agent should detect namespace_quota_exceeded ====="
echo "[scenario_13] Monitor agent at: $AGENT_URL/watch/monitors"
echo "[scenario_13] Check recent alerts: $AGENT_URL/alerts"
echo ""
echo "[scenario_13] Cleanup: run cleanup_13.sh to restore quota and scale down."
echo "  Original replicas saved: $ORIGINAL_REPLICAS"
echo "  QUOTA_CREATED=$QUOTA_CREATED"

# Save state for cleanup script
cat > .scenario_13_state <<EOF
NAMESPACE=$NAMESPACE
QUOTA_NAME=$QUOTA_NAME
TEST_DEPLOYMENT=$TEST_DEPLOYMENT
ORIGINAL_REPLICAS=$ORIGINAL_REPLICAS
QUOTA_CREATED=$QUOTA_CREATED
EOF
echo "[scenario_13] State saved to .scenario_13_state"
