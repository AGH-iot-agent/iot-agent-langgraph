"""
One-shot script to regenerate tests/integration/fixtures/pr71_execution_results.json.

Run this whenever PR #71 or the target CI run changes and you need a fresh
executor snapshot:

    cd iot-agent-langgraph
    python tests/integration/generate_live_fixture.py

Requirements:
  - OPENAI_API_KEY, GITHUB_TOKEN (or gh CLI authenticated)
  - kubectl + helm in PATH, iotag-sbx namespace reachable

The script runs executor_node once against the real GitHub run and writes the
resulting execution_results to the fixture file.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from devops_agent.adapters.github_adapter import GHAdapter  # noqa: E402
from devops_agent.mcp_adapter import MCPAdapter  # noqa: E402
from devops_agent.nodes import executor as _executor_mod  # noqa: E402
from devops_agent.nodes.executor import executor_node  # noqa: E402
from devops_agent.nodes.planner import planner_node  # noqa: E402

_TARGET_REPO = "AGH-iot-agent/iot-agent-login-screen"
_PR_NUMBER = 71
_REAL_RUN_ID = 25969747491
_TARGET_NAMESPACE = os.environ.get("KUBERNETES_NAMESPACE", "iotag-sbx")
_OUT_PATH = Path(__file__).parent / "fixtures" / "pr71_execution_results.json"


def main() -> None:
    real_adapter = MCPAdapter()

    import unittest.mock as _mock
    with _mock.patch.object(_executor_mod, "_adapter", real_adapter):
        gh = GHAdapter()
        pr_resp = gh.get_pull_request(_TARGET_REPO, _PR_NUMBER)
        if pr_resp.get("status") != "ok":
            sys.exit(f"Could not fetch PR #{_PR_NUMBER}: {pr_resp}")

        branch: str = pr_resp["pull_request"]["head"]["ref"]
        print(f"PR #{_PR_NUMBER} branch: {branch}")

        state: dict = {
            "request": f"Fix CI failure on {_TARGET_REPO} branch {branch} (run #{_REAL_RUN_ID})",
            "dry_run": True,
            "event_kind": "github_ci_failure",
            "context": {
                "repo_full_name": _TARGET_REPO,
                "branch": branch,
                "run_id": _REAL_RUN_ID,
                "run_url": f"https://github.com/{_TARGET_REPO}/actions/runs/{_REAL_RUN_ID}",
                "namespace": _TARGET_NAMESPACE,
            },
            "execution_results": [],
            "fix_attempt": 0,
            "max_fix_attempts": 3,
            "validation_history": [],
        }

        state = planner_node(state)
        if not state.get("plan"):
            sys.exit("planner_node produced an empty plan.")

        state = executor_node(state)
        results = state.get("execution_results", [])
        if not results:
            sys.exit("executor_node returned no execution_results.")

    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(results)} entries to {_OUT_PATH}")


if __name__ == "__main__":
    main()
