#!/bin/bash

set -euo pipefail
_THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${_THIS_DIR}"
# shellcheck disable=SC1091
source "${_THIS_DIR}/../_github_setup_token.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"


OWNER="AGH-iot-agent"
REPO="iot-agent-login-screen"
REPO_SLUG="${OWNER}/${REPO}"


git clone "https://${GH_TOKEN}@github.com/${REPO_SLUG}.git"

cd "${REPO}"
RANDOM_SUFFIX=$(date +%s)
BRANCH_NAME="test/scenario_07_${RANDOM_SUFFIX}"

git checkout -b "${BRANCH_NAME}"
configure_git_identity

cp "${SCRIPT_DIR}/values-dev.yaml" ./Helm/values-dev.yaml
cp "${SCRIPT_DIR}/values-sbx.yaml" ./Helm/values-sbx.yaml

git add Helm/values-dev.yaml Helm/values-sbx.yaml
git commit -m "Update Helm values for scenario 07"

git push origin "${BRANCH_NAME}"

gh pr create \
  --repo "${REPO_SLUG}" \
  --title "Test PR for scenario 07: OOMKilled due to insufficient memory limits in Helm values" \
  --body "This PR is created to test the DevOps agent's ability to detect pod OOMKilled failures caused by an insufficient memory limit (1Mi) in the Helm chart values for iot-agent-login-screen." \
  --base main \
  --head "${BRANCH_NAME}"

cd "${SCRIPT_DIR}"
rm -rf "${REPO}"
