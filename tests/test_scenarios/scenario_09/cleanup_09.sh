#!/bin/bash
# Cleanup scenario_09 — delete orphaned test resources from iotag-dev

set -euo pipefail
_THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${_THIS_DIR}"
# shellcheck disable=SC1091
source "${_THIS_DIR}/../_github_setup_token.sh"

NAMESPACE=${NAMESPACE:-iotag-dev}

echo "[cleanup_09] Deleting orphaned-test-cm-* ConfigMaps in $NAMESPACE..."
kubectl get configmap -n "$NAMESPACE" -o name \
  | grep orphaned-test-cm- \
  | xargs -r kubectl delete -n "$NAMESPACE" \
  || echo "[cleanup_09] No orphaned ConfigMaps found."

echo "[cleanup_09] Deleting orphaned-test-secret-* Secrets in $NAMESPACE..."
kubectl get secret -n "$NAMESPACE" -o name \
  | grep orphaned-test-secret- \
  | xargs -r kubectl delete -n "$NAMESPACE" \
  || echo "[cleanup_09] No orphaned Secrets found."

echo "[cleanup_09] Done."
