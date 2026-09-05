_HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_REPO_ROOT="$(cd "${_HELPER_DIR}/../.." && pwd)"

_load_env_file() {
  local f="$1"
  if [[ -f "$f" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$f"
    set +a
  fi
}

if [[ -z "${GH_NON_AGENT_TOKEN:-}" && -z "${TEST_O1_ENV_GH_TOKEN:-}" && -z "${GH_TOKEN:-}" ]]; then
  _load_env_file "${_REPO_ROOT}/.env"
fi

_token_ok() {
  local tok="${1:-}"
  [[ -n "$tok" ]] || return 1
  local code
  code="$(curl -sS -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer ${tok}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -H "User-Agent: iot-agent-scenarios" \
    https://api.github.com/user || true)"
  [[ "$code" == "200" ]]
}

_SETUP_TOKEN="${GH_NON_AGENT_TOKEN:-${TEST_O1_ENV_GH_TOKEN:-}}"
_BOT_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"

if ! _token_ok "${_SETUP_TOKEN}"; then
  if _token_ok "${_BOT_TOKEN}"; then
    echo "[github-setup] GH_NON_AGENT_TOKEN is missing or unauthorized; falling back to GH_TOKEN for fixtures." >&2
    _SETUP_TOKEN="${_BOT_TOKEN}"
  else
    echo "Error: no working GitHub token (GH_NON_AGENT_TOKEN / GH_TOKEN) to create/close issues and PRs." >&2
    exit 1
  fi
fi

GH_NON_AGENT_TOKEN="${_SETUP_TOKEN}"
GH_TOKEN="${_SETUP_TOKEN}"
GITHUB_TOKEN="${_SETUP_TOKEN}"
TEST_O1_ENV_GH_TOKEN="${_SETUP_TOKEN}"
export GH_TOKEN GITHUB_TOKEN TEST_O1_ENV_GH_TOKEN GH_NON_AGENT_TOKEN
export GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-iot-agent-scenario}"
export GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-scenario-tests@iot-agent.local}"
export GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-${GIT_AUTHOR_NAME}}"
export GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-${GIT_AUTHOR_EMAIL}}"
export GIT_TERMINAL_PROMPT=0
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
export AGENT_MODE="${AGENT_MODE:-multi_agent}"

# Call after `git checkout` in a clone: commit identity without touching global git config.
configure_git_identity() {
  git config user.email "${GIT_COMMITTER_EMAIL}"
  git config user.name "${GIT_COMMITTER_NAME}"
}
