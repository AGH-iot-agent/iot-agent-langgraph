#!/bin/bash 

OWNER=AGH-iot-agent
REPO=iot-agent-login-screen

ISSUES=$(curl -s -L \
  -X PATCH \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${TEST_O1_ENV_GH_TOKEN}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/${OWNER}/${REPO}/issues/${ISSUE_NUMBER}" \
  -d '{"state": "closed"}' | jq -r '(.number|tostring) + ": " + .state')

echo "$ISSUES" | jq -r '.[].number' | while read -r ISSUE_NUMBER; do
  echo "Closing issue #${ISSUE_NUMBER}..."
  curl -s -L \
    -X PATCH \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${TEST_O1_ENV_GH_TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/${OWNER}/${REPO}/issues/${ISSUE_NUMBER}" \
    -d '{"state": "closed"}' | jq -r '.number + ": " + .state'
done