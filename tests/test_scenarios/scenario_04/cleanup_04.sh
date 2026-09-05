#!/bin/bash
_THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${_THIS_DIR}"
# shellcheck disable=SC1091
source "${_THIS_DIR}/../_github_setup_token.sh"


OWNER=AGH-iot-agent
REPO=iot-agent-device-api

ISSUES=$(curl -s \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  "https://api.github.com/repos/${OWNER}/${REPO}/issues?state=open&labels=iot-devops-agent&per_page=50")

echo "$ISSUES" | jq -r '.[].number' | while read -r ISSUE_NUMBER; do
  echo "Closing issue #${ISSUE_NUMBER}..."
  curl -s -L \
    -X PATCH \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${GH_TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/${OWNER}/${REPO}/issues/${ISSUE_NUMBER}" \
    -d '{"state": "closed"}' | jq -r '(.number | tostring) + ": " + .state'
done
