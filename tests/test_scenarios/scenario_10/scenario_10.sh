#!/bin/bash
# Scenario 10 — YAML merge conflict in values-dev.yaml via PR
# Expected: agent detects YAML parse error in CI (helm lint fails),
#           identifies conflict markers, proposes fix (pick correct replicaCount), opens PR.

set -euo pipefail
_THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${_THIS_DIR}"
# shellcheck disable=SC1091
source "${_THIS_DIR}/../_github_setup_token.sh"
OWNER=AGH-iot-agent
REPO=iot-agent-logs


RANDOM_SUFFIX=$(date +%s)
BRANCH="test/scenario_10_${RANDOM_SUFFIX}"
TARGET_VALUES_PATH=""
WORKDIR="/tmp/scenario_10_repo_${RANDOM_SUFFIX}"

cleanup() {
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

echo "[scenario_10] Cloning $OWNER/$REPO..."
git clone "https://${GH_TOKEN}@github.com/${OWNER}/${REPO}.git" "$WORKDIR"
cd "$WORKDIR"

git checkout -b "$BRANCH"
git config user.email "${GIT_AUTHOR_EMAIL:-scenario-tests@iot-agent.local}"
git config user.name "${GIT_AUTHOR_NAME:-iot-agent-scenario}"

for candidate in "Helm/values-dev.yaml" "Helm/dev.yaml" "helm/values-dev.yaml" "helm/dev.yaml"; do
  if [[ -f "$candidate" ]]; then
    TARGET_VALUES_PATH="$candidate"
    break
  fi
done

if [[ -z "$TARGET_VALUES_PATH" ]]; then
  echo "[scenario_10] ERROR: could not find target Helm values file in repo." >&2
  exit 1
fi

echo "[scenario_10] Injecting YAML merge conflict into ${TARGET_VALUES_PATH}..."
TARGET_VALUES_PATH="$TARGET_VALUES_PATH" python3 - <<'PYEOF'
import re, sys
import os
path = os.environ["TARGET_VALUES_PATH"]
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

git add "$TARGET_VALUES_PATH"
git commit -m "test(scenario_10): inject merge conflict in ${TARGET_VALUES_PATH}"
git push origin "$BRANCH"

gh pr create \
  --title "Test PR scenario_10: YAML merge conflict in ${TARGET_VALUES_PATH}" \
  --body "This PR contains a merge conflict marker in ${TARGET_VALUES_PATH}. The CI helm lint step should fail. The agent should detect the conflict, pick the correct replicaCount value, and propose a fix." \
  --base main

echo "[scenario_10] Done. PR created on $REPO branch $BRANCH."

