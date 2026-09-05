#!/bin/bash
_THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${_THIS_DIR}"
# shellcheck disable=SC1091
source "${_THIS_DIR}/../_github_setup_token.sh"


OWNER=AGH-iot-agent
REPO=iot-agent-login-screen

# Close all open test PRs for scenario 17
gh pr list --repo ${OWNER}/${REPO} --state open --search "scenario 17" --json number,headRefName | \
  jq -r '.[] | "\(.number) \(.headRefName)"' | \
  while read -r PR_NUMBER BRANCH; do
    echo "Closing PR #${PR_NUMBER} (branch: ${BRANCH})..."
    gh pr close $PR_NUMBER --repo ${OWNER}/${REPO} --delete-branch
  done
