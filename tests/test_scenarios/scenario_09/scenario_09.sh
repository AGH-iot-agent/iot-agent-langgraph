#!/bin/bash
# Scenario 09 — Orphaned K8s resources + GitHub Issue
# Expected: agent detects orphaned ConfigMap/Secret, checks K8s state,
#           and posts a GitHub issue comment with cleanup commands.

set -euo pipefail
export $(grep -v '^#' .env | xargs)

NAMESPACE=${NAMESPACE:-iotag-dev}
GH_TOKEN=${GH_TOKEN:-""}
OWNER=AGH-iot-agent
REPO=iot-agent-authentication

if [ -z "$GH_TOKEN" ]; then
  echo "Error: GH_TOKEN is not set in .env"
  exit 1
fi

echo "[scenario_09] Creating orphaned resources in namespace '$NAMESPACE'..."
kubectl create configmap orphaned-test-cm-$(date +%s) \
  -n "$NAMESPACE" \
  --from-literal=created-by=scenario_09 \
  --from-literal=purpose=testing \
  || echo "[scenario_09] ConfigMap creation skipped (kubectl unavailable)"

kubectl create secret generic orphaned-test-secret-$(date +%s) \
  -n "$NAMESPACE" \
  --from-literal=dummy-key=dummy-value \
  || echo "[scenario_09] Secret creation skipped (kubectl unavailable)"

echo "[scenario_09] Creating GitHub Issue to notify agent..."
ISSUE_TITLE="K8s: Orphaned ConfigMaps and Secrets detected in $NAMESPACE — cleanup required"
ISSUE_BODY=$(cat <<'EOF'
**Problem:** After a recent deployment cleanup, several orphaned ConfigMaps and Secrets were left
in the `iotag-dev` namespace. These are not referenced by any active deployment or pod and are
causing confusion in the namespace.

**Observed:**
- `orphaned-test-cm-*` ConfigMaps with label `created-by=scenario_09`
- `orphaned-test-secret-*` Secrets with label `created-by=scenario_09`

**Expected action:** List orphaned resources, verify they are not in use, and propose
`kubectl delete` commands or a cleanup job.
EOF
)

JSON=$(jq -n \
  --arg title "$ISSUE_TITLE" \
  --arg body "$ISSUE_BODY" \
  '{title: $title, body: $body, labels: ["bug", "iot-devops-agent"]}')

curl -s -L \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/${OWNER}/${REPO}/issues \
  -d "$JSON" | jq -r '.html_url // .message'

echo "[scenario_09] Done. Agent should detect and respond to the issue."

