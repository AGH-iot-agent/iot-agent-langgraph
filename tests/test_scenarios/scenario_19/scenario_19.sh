#!/bin/bash
# Scenario 19 — Compound Helm faults via PR (max 3 independent problems)
# 1) replicaCount: "not-a-number" (schema / CI-loud)
# 2) livenessProbe.path: /invalid-path-for-liveness-probe (health, quiet)
# 3) VirtualService host: wrong-host.iotag-dev.com (Istio cross-env, quiet)
# Expected: CI fails; agent must name ALL three families. Single-agent typically
# latches onto the schema error from CI logs and misses the quiet faults.

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
BRANCH_NAME="test/scenario_19_${RANDOM_SUFFIX}"
WORKDIR="/tmp/scenario_19_repo_${RANDOM_SUFFIX}"

echo "[scenario_19] Cloning ${REPO_SLUG}..."
git clone "https://${GH_TOKEN}@github.com/${REPO_SLUG}.git" "${WORKDIR}"

cd "${WORKDIR}"
git checkout -b "${BRANCH_NAME}" "origin/${BASE_BRANCH}"
configure_git_identity

echo "[scenario_19] Injecting three independent Helm faults into values-sbx..."
cp "${SCRIPT_DIR}/values-dev.yaml" "./Helm/values-dev.yaml"
cp "${SCRIPT_DIR}/values-sbx.yaml" "./Helm/values-sbx.yaml"

git add Helm/values-dev.yaml Helm/values-sbx.yaml
git commit -m "test(scenario_19): compound replicaCount + probe path + wrong VS host"
git push origin "${BRANCH_NAME}"

gh pr create \
  --repo "${REPO_SLUG}" \
  --title "Test PR scenario_19: compound Helm (replicaCount + probe + wrong-host)" \
  --body "This PR injects THREE independent faults in Helm/values-sbx.yaml: invalid replicaCount, invalid livenessProbe path, and VirtualService host wrong-host.iotag-dev.com. CI should fail. The agent must diagnose all three — a single-fault comment is incorrect." \
  --base "${BASE_BRANCH}" \
  --head "${BRANCH_NAME}"

cd /tmp
rm -rf "${WORKDIR}"

echo "[scenario_19] Done. PR created for ${REPO_SLUG} on branch ${BRANCH_NAME}."
