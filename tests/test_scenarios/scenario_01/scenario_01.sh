#!/bin/bash
set -euo pipefail

TEST_O1_ENV_GH_TOKEN="$1"

if [ -z "$TEST_O1_ENV_GH_TOKEN" ]; then
  echo "Error: token argument is required (pass GH_NON_AGENT_TOKEN)." >&2
  exit 1
fi

ISSUE_TITLE="Infrastructure CI failure: 'npm ci' fails with 'ENOENT: no such file or directory, open package-lock.json'"
OWNER=AGH-iot-agent
REPO=iot-agent-login-screen

BODY=$(cat <<'EOF'
**Hello,**

I'm having an issue with the CI pipeline for the infrastructure repository. The 'npm ci' command is failing with the following error: `ENOENT: no such file or directory, open 'package-lock.json'`
EOF
)

JSON=$(jq -n \
  --arg title "$ISSUE_TITLE" \
  --arg body "$BODY" \
  '{title: $title, body: $body, labels: ["bug", "iot-devops-agent"]}')

HTTP_CODE=$(curl -sL -w "%{http_code}" -o /tmp/scenario_01_response.json \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${TEST_O1_ENV_GH_TOKEN}" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/${OWNER}/${REPO}/issues" \
  -d "$JSON")

if [ "$HTTP_CODE" -ge 300 ]; then
  echo "GitHub API returned HTTP $HTTP_CODE:" >&2
  cat /tmp/scenario_01_response.json >&2
  exit 1
fi

cat /tmp/scenario_01_response.json