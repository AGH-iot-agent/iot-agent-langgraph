#!/bin/bash

export $(grep -v '^#' .env | xargs)

OWNER=AGH-iot-agent
REPO=iot-agent-alert-api

# Close all open test PRs for scenario 05
gh pr list --repo ${OWNER}/${REPO} --state open --search "scenario 05" --json number,headRefName | \
  jq -r '.[] | "\(.number) \(.headRefName)"' | \
  while read -r PR_NUMBER BRANCH; do
    echo "Closing PR #${PR_NUMBER} (branch: ${BRANCH})..."
    gh pr close $PR_NUMBER --repo ${OWNER}/${REPO} --delete-branch
  done
