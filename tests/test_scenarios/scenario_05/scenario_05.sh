#!/bin/bash

set -euo pipefail
_THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${_THIS_DIR}"
# shellcheck disable=SC1091
source "${_THIS_DIR}/../_github_setup_token.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"


OWNER="AGH-iot-agent"
REPO="iot-agent-alert-api"
REPO_SLUG="${OWNER}/${REPO}"


RANDOM_SUFFIX=$(date +%s)
BRANCH_NAME="test/scenario_05_${RANDOM_SUFFIX}"
WORKDIR="/tmp/scenario_05_repo_${RANDOM_SUFFIX}"

git clone "https://${GH_TOKEN}@github.com/${REPO_SLUG}.git" "${WORKDIR}"

cd "${WORKDIR}"

git checkout -b "${BRANCH_NAME}"
git config user.email "${GIT_AUTHOR_EMAIL:-scenario-tests@iot-agent.local}"
git config user.name "${GIT_AUTHOR_NAME:-iot-agent-scenario}"

rm ./Helm/values-dev.yaml ./Helm/values-sbx.yaml
cp "${SCRIPT_DIR}/values-dev.yaml" ./Helm/values-dev.yaml
cp "${SCRIPT_DIR}/values-sbx.yaml" ./Helm/values-sbx.yaml

git add .
git commit -m "Update Helm values for scenario 05"

git push origin "${BRANCH_NAME}"

gh pr create \
  --repo "${REPO_SLUG}" \
  --title "Test PR for scenario 05: CrashLoopBackOff due to incorrect liveness probe port in Helm values" \
  --body "This PR is created to test the DevOps agent's ability to detect CrashLoopBackOff caused by an incorrect liveness probe port (9999) in the Helm chart values for iot-agent-alert-api. The correct port name is 'default-service'." \
  --base main \
  --head "${BRANCH_NAME}"

cd /tmp
rm -rf "${WORKDIR}"
