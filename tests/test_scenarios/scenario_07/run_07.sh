#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
MODE="${1:-live-test}"

if [[ -f "${SCRIPT_DIR}/.env" ]]; then
  export $(grep -v '^#' "${SCRIPT_DIR}/.env" | xargs)
fi

export TEST_SCENARIO07_TARGET_REPO="${TEST_SCENARIO07_TARGET_REPO:-AGH-iot-agent/iot-agent-login-screen}"

usage() {
  cat <<'USAGE'
Usage: bash run_07.sh [live-test|pr-only|cleanup]

Modes:
  live-test  Run pytest scenario_07.py (default).
  pr-only    Create a scenario PR using scenario_07.sh.
  cleanup    Close open scenario 07 PRs using cleanup_07.sh.
USAGE
}

require_env_for_live_test() {
  if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo "OPENAI_API_KEY is required for live-test mode." >&2
    exit 1
  fi

  if [[ -z "${GH_NON_AGENT_TOKEN:-${TEST_O1_ENV_GH_TOKEN:-}}" ]]; then
    echo "GH_NON_AGENT_TOKEN is required to create fixture PRs (set in .env)." >&2
    exit 1
  fi
  if [[ -z "${GH_TOKEN:-}" ]]; then
    echo "GH_TOKEN (agent) is required for live-test mode." >&2
    exit 1
  fi
}

case "${MODE}" in
  live-test)
    require_env_for_live_test
    cd "${REPO_ROOT}"
    pytest -s -o log_cli=true -o log_cli_level=INFO tests/test_scenarios/scenario_07/scenario_07.py
    ;;
  pr-only)
    cd "${SCRIPT_DIR}"
    bash "${SCRIPT_DIR}/scenario_07.sh"
    ;;
  cleanup)
    cd "${SCRIPT_DIR}"
    bash "${SCRIPT_DIR}/cleanup_07.sh"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "Unknown mode: ${MODE}" >&2
    usage
    exit 2
    ;;
esac
