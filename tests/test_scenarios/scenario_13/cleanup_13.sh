#!/bin/bash
# Cleanup for Scenario 13 — restores ResourceQuota and deployment replicas

set -euo pipefail

if [ ! -f .scenario_13_state ]; then
  echo "[cleanup_13] No state file found — nothing to restore."
  exit 0
fi

export $(grep -v '^#' .scenario_13_state | xargs)

echo "[cleanup_13] Restoring $TEST_DEPLOYMENT to $ORIGINAL_REPLICAS replicas..."
kubectl scale deployment "$TEST_DEPLOYMENT" -n "$NAMESPACE" \
  --replicas="$ORIGINAL_REPLICAS"

if [ "$QUOTA_CREATED" = "true" ]; then
  echo "[cleanup_13] Removing ResourceQuota $QUOTA_NAME (was created by scenario)..."
  kubectl delete resourcequota "$QUOTA_NAME" -n "$NAMESPACE" || true
else
  echo "[cleanup_13] Restoring original ResourceQuota (removing pods limit patch)..."
  # Remove pods restriction by setting a large value
  kubectl patch resourcequota "$QUOTA_NAME" -n "$NAMESPACE" \
    --type merge \
    -p '{"spec":{"hard":{"pods":"100"}}}'
fi

rm -f .scenario_13_state
echo "[cleanup_13] Done."
