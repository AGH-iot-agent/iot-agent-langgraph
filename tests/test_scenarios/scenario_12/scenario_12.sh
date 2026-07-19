#!/bin/bash
# Scenario 12 — Wrong VirtualService host in values-sbx.yaml via PR
# Expected: agent detects CI deploy failure (VirtualService misconfiguration),
#           identifies wrong host/namespace in values-sbx.yaml,
#           proposes fix (correct VS host pattern), opens PR to PR branch.

set -euo pipefail
export $(grep -v '^#' .env | xargs)

GH_TOKEN=${GH_TOKEN:-""}
OWNER=AGH-iot-agent
REPO=iot-agent-dashboard-api

if [ -z "$GH_TOKEN" ]; then
  echo "Error: GH_TOKEN is not set in .env"
  exit 1
fi

RANDOM_SUFFIX=$(date +%s)
BRANCH="test/scenario_12_${RANDOM_SUFFIX}"

echo "[scenario_12] Cloning $OWNER/$REPO..."
git clone "https://${GH_TOKEN}@github.com/${OWNER}/${REPO}.git" /tmp/scenario_12_repo
cd /tmp/scenario_12_repo

git checkout -b "$BRANCH"

echo "[scenario_12] Injecting wrong VS host into Helm/values-sbx.yaml..."
python3 - <<'PYEOF'
import re
path = "Helm/values-sbx.yaml"
with open(path) as f:
    content = f.read()
# Corrupt the VS host to a wrong value (cross-environment pollution + wrong domain)
bad_content = re.sub(
    r'(host:\s*)([^\n]+iotag-sbx\.com)',
    r'\1wrong-host.iotag-dev.com',
    content,
    count=1,
)
if bad_content == content:
    print("[scenario_12] WARNING: no VS host pattern found, injecting virtualService section")
    bad_content = content + "\nvirtualService:\n  host: wrong-host.iotag-dev.com\n"
with open(path, 'w') as f:
    f.write(bad_content)
print("[scenario_12] Corrupted VS host injected into", path)
PYEOF

git add Helm/values-sbx.yaml
git commit -m "test(scenario_12): wrong VirtualService host in values-sbx.yaml"
git push origin "$BRANCH"

gh pr create \
  --title "Test PR scenario_12: wrong VirtualService host in values-sbx.yaml" \
  --body "This PR intentionally sets a wrong VirtualService host (iotag-dev domain in sbx values). The CI deploy step should fail. The agent should detect the misconfiguration and propose the correct host pattern." \
  --base main

cd /tmp && rm -rf /tmp/scenario_12_repo
echo "[scenario_12] Done. PR created on $REPO branch $BRANCH."

