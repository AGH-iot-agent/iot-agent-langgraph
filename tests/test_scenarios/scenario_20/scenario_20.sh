#!/bin/bash
# Scenario 20 — Compound Helm faults via PR (max 3 independent problems)
# 1) replicaCount: "not-a-number" (schema / CI-loud)
# 2) resources.limits.memory: 1Mi (looks like OOMKilled)
# 3) livenessProbe.port: 9999 (looks like CrashLoopBackOff)
# Expected: CI fails on schema; agent must name ALL three. Single-agent mixes
# OOM vs probe vs replica stories or only quotes the CI schema error.

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
BRANCH_NAME="test/scenario_20_${RANDOM_SUFFIX}"
WORKDIR="/tmp/scenario_20_repo_${RANDOM_SUFFIX}"

echo "[scenario_20] Cloning ${REPO_SLUG}..."
git clone "https://${GH_TOKEN}@github.com/${REPO_SLUG}.git" "${WORKDIR}"

cd "${WORKDIR}"
git checkout -b "${BRANCH_NAME}" "origin/${BASE_BRANCH}"
configure_git_identity

echo "[scenario_20] Injecting three conflicting-domain Helm faults into values-sbx..."
cp "${SCRIPT_DIR}/values-dev.yaml" "./Helm/values-dev.yaml"
cp "${SCRIPT_DIR}/values-sbx.yaml" "./Helm/values-sbx.yaml"

git add Helm/values-dev.yaml Helm/values-sbx.yaml
git commit -m "test(scenario_20): compound replicaCount + 1Mi memory + probe port 9999"
git push origin "${BRANCH_NAME}"

gh pr create \
  --repo "${REPO_SLUG}" \
  --title "Test PR scenario_20: compound Helm (replicaCount + 1Mi + port 9999)" \
  --body "This PR injects THREE independent faults in Helm/values-sbx.yaml: invalid replicaCount, memory limit 1Mi, and livenessProbe port 9999. Symptoms look like schema failure AND OOM AND CrashLoop at once. The agent must diagnose all three — picking only one story is incorrect." \
  --base "${BASE_BRANCH}" \
  --head "${BRANCH_NAME}"

cd /tmp
rm -rf "${WORKDIR}"

echo "[scenario_20] Done. PR created for ${REPO_SLUG} on branch ${BRANCH_NAME}."
