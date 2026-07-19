#!/bin/bash

set -x

export $(grep -v '^#' .env | xargs)

GH_TOKEN=${GH_TOKEN:-""}

if [ -z "$GH_TOKEN" ]; then
  echo "Error: GH_TOKEN environment variable is not set."
  exit 1
fi

ISSUE_TITLE="CI failure: GitHub Actions reusable workflow — 'env' context not available in 'with' block"
OWNER=AGH-iot-agent
REPO=iot-agent-logs
BODY=$(<issue_body.md)

JSON=$(jq -n \
  --arg title "$ISSUE_TITLE" \
  --arg body "$BODY" \
  '{title: $title, body: $body, labels: ["bug", "iot-devops-agent"]}')

curl -L \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H "X-GitHub-Api-Version: 2026-03-10" \
  https://api.github.com/repos/${OWNER}/${REPO}/issues \
  -d "$JSON"
