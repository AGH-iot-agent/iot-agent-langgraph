#!/bin/bash
# Cleanup scenario_10 — close open test PRs on iot-agent-logs

set -euo pipefail
_THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${_THIS_DIR}"
# shellcheck disable=SC1091
source "${_THIS_DIR}/../_github_setup_token.sh"

OWNER=${OWNER:-AGH-iot-agent}
REPO=${REPO:-iot-agent-logs}

echo "[cleanup_10] Closing open 'test/scenario_10_*' PRs on $OWNER/$REPO..."
gh pr list \
  --repo "$OWNER/$REPO" \
  --state open \
  --json number,headRefName \
  --jq '.[] | select(.headRefName | startswith("test/scenario_10_")) | .number' \
  | while read -r PR_NUM; do
      echo "  Closing PR #$PR_NUM..."
      gh pr close "$PR_NUM" --repo "$OWNER/$REPO" --delete-branch
    done

echo "[cleanup_10] Done."
