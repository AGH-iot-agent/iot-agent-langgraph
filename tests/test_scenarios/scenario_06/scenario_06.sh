#!/bin/bash

set -euo pipefail
_THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${_THIS_DIR}"
# shellcheck disable=SC1091
source "${_THIS_DIR}/../_github_setup_token.sh"



ISSUE_TITLE="CI failure: GitHub Actions reusable workflow — 'env' context not available in 'with' block"
OWNER=AGH-iot-agent
REPO=iot-agent-logs
BODY=$(<issue_body.md)

JSON=$(jq -n \
  --arg title "$ISSUE_TITLE" \
  --arg body "$BODY" \
  '{title: $title, body: $body, labels: ["bug", "iot-devops-agent"]}')

RESP_FILE=$(mktemp)
HTTP_CODE=$(curl -sS -L -w "%{http_code}" -o "$RESP_FILE" \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/${OWNER}/${REPO}/issues \
  -d "$JSON")

if [ "$HTTP_CODE" -ge 300 ]; then
  echo "GitHub API returned HTTP $HTTP_CODE" >&2
  cat "$RESP_FILE" >&2
  rm -f "$RESP_FILE"
  exit 1
fi

cat "$RESP_FILE"
rm -f "$RESP_FILE"
