#!/bin/bash

export $(grep -v '^#' .env | xargs)

OWNER=AGH-iot-agent
REPO=iot-agent-stream-worker

# Close all open test PRs for scenario 07
gh pr list --repo ${OWNER}/${REPO} --state open --search "scenario 07" --json number,headRefName | \
  jq -r '.[] | "\(.number) \(.headRefName)"' | \
  while read -r PR_NUMBER BRANCH; do
    echo "Closing PR #${PR_NUMBER} (branch: ${BRANCH})..."
    gh pr close $PR_NUMBER --repo ${OWNER}/${REPO} --delete-branch
  done
