#!/bin/bash
# Scenario 10 — YAML merge conflict in values-dev.yaml via PR
# Expected: agent detects YAML parse error in CI (helm lint fails),
#           identifies conflict markers, proposes fix (pick correct replicaCount), opens PR.

set -euo pipefail
export $(grep -v '^#' .env | xargs)

GH_TOKEN=${GH_TOKEN:-""}
OWNER=AGH-iot-agent
REPO=iot-agent-logs

if [ -z "$GH_TOKEN" ]; then
  echo "Error: GH_TOKEN is not set in .env"
  exit 1
fi

RANDOM_SUFFIX=$(date +%s)
BRANCH="test/scenario_10_${RANDOM_SUFFIX}"

echo "[scenario_10] Cloning $OWNER/$REPO..."
git clone "https://${GH_TOKEN}@github.com/${OWNER}/${REPO}.git" /tmp/scenario_10_repo
cd /tmp/scenario_10_repo

git checkout -b "$BRANCH"

echo "[scenario_10] Injecting YAML merge conflict into Helm/values-dev.yaml..."
python3 - <<'PYEOF'
import re, sys
path = "Helm/values-dev.yaml"
with open(path) as f:
    content = f.read()
# Replace the replicaCount line with a conflict block
conflict = (
    "<<<<<<< HEAD\n"
    "replicaCount: 2\n"
    "=======\n"
    "replicaCount: 3\n"
    ">>>>>>> branch\n"
)
new_content = re.sub(r'^replicaCount:.*$', conflict.rstrip(), content, flags=re.MULTILINE, count=1)
if new_content == content:
    print("[scenario_10] WARNING: replicaCount not found, appending conflict at end")
    new_content = content + "\n" + conflict
with open(path, 'w') as f:
    f.write(new_content)
print("[scenario_10] Conflict injected into", path)
PYEOF

git add Helm/values-dev.yaml
git commit -m "test(scenario_10): inject merge conflict in values-dev.yaml"
git push origin "$BRANCH"

gh pr create \
  --title "Test PR scenario_10: YAML merge conflict in values-dev.yaml" \
  --body "This PR contains a merge conflict marker in Helm/values-dev.yaml. The CI helm lint step will fail. The agent should detect the conflict, pick the correct replicaCount value, and propose a fix." \
  --base main

cd /tmp && rm -rf /tmp/scenario_10_repo
echo "[scenario_10] Done. PR created on $REPO branch $BRANCH."

