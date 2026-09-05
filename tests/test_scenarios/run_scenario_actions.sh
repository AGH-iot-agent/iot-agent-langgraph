#!/bin/bash
# Run scenario action scripts (.sh) without Python tests.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

usage() {
  cat <<'EOF'
Usage:
  bash run_scenario_actions.sh [--continue-on-error] <target>

Targets:
  01..20         Run one scenario action script
  all            Run all scenario action scripts in order (01..20)
  all-pr         Run PR scenarios only: 02 05 07 10 12 17 19 20
  all-issue      Run issue scenarios only: 01 04 06 08 09 15 16
  all-monitor    Run monitor/load scenarios only: 03 11 13 14 18

Notes:
  - This launcher runs only shell scenario actions, never *.py tests.
  - For scenario_01 the script requires token arg; this launcher passes GH_NON_AGENT_TOKEN.
  - Ensure GH_NON_AGENT_TOKEN is exported (fixture issues/PRs). GH_TOKEN remains the agent bot.
  - With --continue-on-error, launcher runs all selected scenarios and reports failures at the end.
EOF
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Error: required command not found: $cmd" >&2
    exit 1
  fi
}

run_one() {
  local id="$1"
  local dir="scenario_${id}"
  local script="${dir}/scenario_${id}.sh"
  local rc=0

  if [[ ! -f "${script}" ]]; then
    echo "[scenario_${id}] SKIP: missing script ${script}"
    return 0
  fi

  echo "===== RUN scenario_${id} ====="

  if [[ "${id}" == "01" ]]; then
    local issue_token="${GH_NON_AGENT_TOKEN:-${TEST_O1_ENV_GH_TOKEN:-}}"
    if [[ -z "${issue_token}" ]]; then
      echo "[scenario_01] FAIL: GH_NON_AGENT_TOKEN or TEST_O1_ENV_GH_TOKEN is required for scenario_01." >&2
      return 1
    fi
    (cd "${dir}" && bash "scenario_${id}.sh" "${issue_token}")
    rc=$?
  else
    (cd "${dir}" && bash "scenario_${id}.sh")
    rc=$?
  fi

  if [[ $rc -eq 0 ]]; then
    echo "===== DONE scenario_${id} ====="
  else
    echo "===== ERROR scenario_${id} (rc=${rc}) =====" >&2
  fi

  return "$rc"
}

run_many() {
  local continue_on_error="$1"
  shift
  local failed=()
  local id
  local rc

  for id in "$@"; do
    set +e
    run_one "$id"
    rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
      failed+=("$id")
      echo "===== FAIL scenario_${id} (rc=${rc}) =====" >&2
      if [[ "$continue_on_error" != "1" ]]; then
        return "$rc"
      fi
    fi
  done

  if [[ ${#failed[@]} -gt 0 ]]; then
    echo "Scenarios with failures: ${failed[*]}" >&2
    return 1
  fi

  return 0
}

main() {
  local continue_on_error=0
  if [[ "${1:-}" == "--continue-on-error" ]]; then
    continue_on_error=1
    shift
  fi

  local target="${1:-}"
  if [[ -z "${target}" ]]; then
    usage
    exit 1
  fi

  require_cmd git
  require_cmd bash

  case "${target}" in
    01|02|03|04|05|06|07|08|09|10|11|12|13|14|15|16|17|18|19|20)
      run_many "${continue_on_error}" "${target}"
      ;;
    all)
      run_many "${continue_on_error}" 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20
      ;;
    all-pr)
      run_many "${continue_on_error}" 02 05 07 10 12 17 19 20
      ;;
    all-issue)
      run_many "${continue_on_error}" 01 04 06 08 09 15 16
      ;;
    all-monitor)
      run_many "${continue_on_error}" 03 11 13 14 18
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"