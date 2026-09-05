#!/bin/bash
# Scenario 02 — Broken Helm values via PR
# Expected: CI fails on Helm validation and agent diagnoses values issue.

set -euo pipefail
_THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${_THIS_DIR}"
# shellcheck disable=SC1091
source "${_THIS_DIR}/../_github_setup_token.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

OWNER="${OWNER:-AGH-iot-agent}"
REPO="${REPO:-iot-agent-login-screen}"
REPO_SLUG="${OWNER}/${REPO}"
BASE_BRANCH="${BASE_BRANCH:-main}"


RANDOM_SUFFIX="$(date +%s)"
BRANCH_NAME="test/scenario_02_${RANDOM_SUFFIX}"
WORKDIR="/tmp/scenario_02_repo_${RANDOM_SUFFIX}"

echo "[scenario_02] Cloning ${REPO_SLUG}..."
git clone "https://${GH_TOKEN}@github.com/${REPO_SLUG}.git" "${WORKDIR}"

cd "${WORKDIR}"
git checkout -b "${BRANCH_NAME}" "origin/${BASE_BRANCH}"
configure_git_identity

echo "[scenario_02] Mutating Helm/values-sbx.yaml only (values-dev stays valid)..."
cp "${SCRIPT_DIR}/values-sbx.yaml" "./Helm/values-sbx.yaml"

git add Helm/values-sbx.yaml
git commit -m "test(scenario_02): inject replicaCount string + liveness path on values-sbx"
git push origin "${BRANCH_NAME}"

gh pr create \
  --repo "${REPO_SLUG}" \
  --title "Test PR scenario_02: broken Helm values (liveness/replica errors)" \
  --body "This PR intentionally injects broken Helm values for scenario_02. It should fail CI validation so the agent can diagnose the root cause and propose a fix." \
  --base "${BASE_BRANCH}" \
  --head "${BRANCH_NAME}"

cd /tmp
rm -rf "${WORKDIR}"

echo "[scenario_02] Done. PR created for ${REPO_SLUG} on branch ${BRANCH_NAME}."