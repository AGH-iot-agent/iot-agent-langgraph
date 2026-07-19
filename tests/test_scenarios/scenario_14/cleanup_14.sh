#!/bin/bash
# Cleanup for Scenario 14 — removes dummy files written during disk pressure test

set -euo pipefail

if [ ! -f .scenario_14_state ]; then
  echo "[cleanup_14] No state file found — manual cleanup may be needed."
  exit 1
fi

export $(grep -v '^#' .scenario_14_state | xargs)

echo "[cleanup_14] Removing dummy files from pod $TARGET_POD ($WRITE_PATH)..."
kubectl exec -n "$NAMESPACE" "$TARGET_POD" -- \
  rm -rf "$WRITE_PATH" 2>/dev/null || \
  kubectl exec -n "$NAMESPACE" "$TARGET_POD" -- \
  /bin/sh -c "rm -rf $WRITE_PATH" || \
  echo "[cleanup_14] Could not remove files — pod may have restarted. Files will be gone after pod restart."

rm -f .scenario_14_state
echo "[cleanup_14] Done. kubelet_volume_stats metrics will normalise within ~60s."
