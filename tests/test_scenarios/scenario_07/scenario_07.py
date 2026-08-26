from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from devops_agent.adapters.github_adapter import GHAdapter
from devops_agent.adapters.k8s_adapter import K8sAdapter
from devops_agent.event import AgentEvent
from devops_agent.graph import build_graph
from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.watchdog import AgentWatchdog

TARGET_REPO = os.environ.get("TEST_SCENARIO07_TARGET_REPO", "AGH-iot-agent/iot-agent-login-screen")
BASE_BRANCH = os.environ.get("TEST_SCENARIO07_BASE_BRANCH", "main")
TARGET_NAMESPACE = os.environ.get("TEST_SCENARIO07_TARGET_NAMESPACE", "iotag-dev")
KUBECONFIG = os.environ.get("TEST_SCENARIO07_KUBECONFIG")
OOM_WAIT_TIMEOUT_S = int(os.environ.get("TEST_SCENARIO07_OOM_WAIT_TIMEOUT_S", "600"))
OOM_POLL_INTERVAL_S = int(os.environ.get("TEST_SCENARIO07_OOM_POLL_INTERVAL_S", "15"))
SCENARIO_DIR = Path(__file__).parent
BROKEN_DEV_VALUES_PATH = SCENARIO_DIR / "values-dev.yaml"
BROKEN_SBX_VALUES_PATH = SCENARIO_DIR / "values-sbx.yaml"


class _RecordingAdapter:
    """Records outgoing GitHub write calls for deterministic assertions."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        service: str,
        action: str,
        args: dict[str, Any],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "service": service,
                "action": action,
                "args": args,
                "dry_run": dry_run,
            }
        )
        if service == "github" and action == "create_issue_comment":
            return {"status": "ok", "id": 1}
        return {"status": "ok"}


class _GraphWithoutSummary:
    def invoke(self, _state: dict[str, Any]) -> dict[str, Any]:
        return {}


class _GraphWithFixPR:
    def invoke(self, _state: dict[str, Any]) -> dict[str, Any]:
        return {
            "final_summary": (
                "OOMKilled is caused by an underprovisioned memory limit in Helm values; "
                "the container needs a higher memory budget and a safer baseline is 512Mi. "
                "Increase resources.limits.memory and requests.memory in Helm/values-dev.yaml."
            ),
            "pr_url": f"https://github.com/{TARGET_REPO}/pull/999",
        }


def _require_env() -> None:
    if not TARGET_REPO:
        pytest.fail("Missing required environment variable: TEST_SCENARIO07_TARGET_REPO")


def _build_pr_event(repo: str, pr_number: int, branch: str, body: str) -> AgentEvent:
    return AgentEvent(
        kind="github_pr",
        title=f"{repo}#{pr_number}: Test PR for scenario 07: OOMKilled due to insufficient memory limits in Helm values",
        body=body,
        context={
            "repo_full_name": repo,
            "pr_number": pr_number,
            "branch": branch,
        },
    )


def _wait_for_real_oomkilled(k8s_adapter: K8sAdapter, namespace: str, timeout_s: int, poll_interval_s: int) -> dict[str, Any]:
    """Wait until the live cluster reports a real OOMKilled pod in the target namespace.

    This mirrors the scenario_02 pattern: the test must wait for the environment to fail,
    and only then dispatch the watchdog event.
    """
    deadline = time.time() + timeout_s
    last_seen: dict[str, Any] = {}

    while time.time() < deadline:
        pods_resp = k8s_adapter.get_pods(namespace)
        if pods_resp.get("status") == "ok":
            for pod in pods_resp.get("pods", []):
                restarts = int(pod.get("restarts", 0))
                phase = str(pod.get("phase", "")).lower()
                if restarts > 0 or phase not in {"running", "succeeded"}:
                    events_resp = k8s_adapter.get_events(namespace)
                    if events_resp.get("status") == "ok":
                        for event in events_resp.get("events", []):
                            reason = str(event.get("reason") or "")
                            if reason.lower() in {"oomkilling", "oomkilled"}:
                                return {"pod": pod, "event": event}

        events_resp = k8s_adapter.get_events(namespace)
        if events_resp.get("status") == "ok":
            for event in events_resp.get("events", []):
                reason = str(event.get("reason") or "")
                if reason.lower() in {"oomkilling", "oomkilled"}:
                    return {"event": event}
                last_seen = {"event": event}

        time.sleep(poll_interval_s)

    pytest.fail(
        f"Timed out after {timeout_s}s waiting for a real OOMKilled event in namespace '{namespace}'. "
        f"Last seen state: {last_seen}"
    )


@pytest.fixture(scope="module")
def gh() -> GHAdapter:
    return GHAdapter()


@pytest.fixture(scope="module")
def k8s_adapter() -> K8sAdapter:
    return K8sAdapter(kubeconfig=KUBECONFIG)


@pytest.fixture(scope="module")
def mcp_adapter() -> MCPAdapter:
    return MCPAdapter()


@pytest.fixture(scope="module")
def live_scenario07_pr(gh: GHAdapter) -> Any:
    _require_env()

    branch = f"test/scenario_07_{int(time.time())}"

    base_sha_result = gh.get_branch_sha(TARGET_REPO, BASE_BRANCH)
    assert base_sha_result.get("status") == "ok", (
        f"Could not resolve HEAD SHA for {TARGET_REPO}@{BASE_BRANCH}: {base_sha_result}"
    )
    base_sha = base_sha_result["sha"]

    branch_result = gh.create_branch(TARGET_REPO, branch, base_sha)
    assert branch_result.get("status") == "ok", (
        f"Failed to create branch {branch} on {TARGET_REPO}: {branch_result}"
    )

    broken_dev = BROKEN_DEV_VALUES_PATH.read_text(encoding="utf-8")
    fixed_sbx = BROKEN_SBX_VALUES_PATH.read_text(encoding="utf-8")

    for path, content in (
        ("Helm/values-dev.yaml", broken_dev),
        ("Helm/values-sbx.yaml", fixed_sbx),
    ):
        commit_result = gh.commit_file(
            repo=TARGET_REPO,
            path=path,
            content=content,
            branch=branch,
            message=f"Update Helm values for scenario 07 ({path})",
        )
        assert commit_result.get("status") == "ok", (
            f"Failed to commit {path} on {branch}: {commit_result}"
        )

    pr_result = gh.create_pull_request(
        repo=TARGET_REPO,
        title="[true-e2e-test] scenario 07: OOMKilled due to insufficient memory limits in Helm values",
        body=(
            "Automated TRUE e2e test PR for OOMKilled memory-limit regression. "
            "This PR intentionally sets Helm/values-dev.yaml to memory: 32Mi, which "
            "is too low for the target container. The agent should diagnose the OOMKilled "
            "root cause and propose a fix to increase memory limits in the Helm chart."
        ),
        branch=branch,
        base=BASE_BRANCH,
        dry_run=False,
    )
    assert pr_result.get("status") == "ok", f"Failed to open PR for {branch}: {pr_result}"
    pr_url = pr_result["pr_url"]
    pr_number = int(pr_url.rstrip("/").rsplit("/", 1)[-1])

    # Best-effort label addition. Some GitHub orgs fail on classic Projects access
    # during PR editing, so we do not hard-fail the scenario on this optional metadata.
    subprocess.run(
        [
            "gh",
            "api",
            f"/repos/{TARGET_REPO}/issues/{pr_number}/labels",
            "--method",
            "POST",
            "--input",
            "-",
        ],
        input='["iot-devops-agent"]',
        capture_output=True,
        text=True,
        check=False,
    )

    info: dict[str, Any] = {
        "branch": branch,
        "pr_number": pr_number,
        "pr_url": pr_url,
    }

    try:
        yield info
    finally:
        gh.close_pull_request(TARGET_REPO, pr_number)
        gh.delete_branch(TARGET_REPO, branch)


@pytest.mark.integration
def test_scenario07_appends_memory_fix_recommendation_for_pr_comment() -> None:
    issue_body = (
        "This PR sets resources.limits.memory to 32Mi in Helm/values-dev.yaml for a Spring Boot app; "
        "after deployment the container is OOMKilled with exit code 137."
    )
    adapter = _RecordingAdapter()
    watchdog = AgentWatchdog(adapter=adapter, graph=_GraphWithFixPR(), monitors=[])

    watchdog._dispatch_inner(_build_pr_event(TARGET_REPO, 7, "test/scenario_07_demo", issue_body))

    issue_comment_calls = [
        c for c in adapter.calls if c["service"] == "github" and c["action"] == "create_issue_comment"
    ]
    assert len(issue_comment_calls) == 1

    comment_body = issue_comment_calls[0]["args"]["body"]
    assert "## gh_action_bot" in comment_body
    assert "OOMKilled" in comment_body or "memory" in comment_body.lower()
    assert "Helm/values-dev.yaml" in comment_body or "resources.limits.memory" in comment_body
    assert "512Mi" in comment_body or "256Mi" in comment_body


@pytest.mark.integration
@pytest.mark.e2e
def test_scenario07_real_agent_comments_on_live_pr(
    live_scenario07_pr: dict[str, Any],
    mcp_adapter: MCPAdapter,
    gh: GHAdapter,
    k8s_adapter: K8sAdapter,
) -> None:
    pr_number = live_scenario07_pr["pr_number"]
    branch = live_scenario07_pr["branch"]

    # IMPORTANT: mirror scenario_02. The test must wait for the environment to fail,
    # then only dispatch the agent. If the cluster never reports OOMKilled, the test fails
    # loudly instead of pretending the agent handled a problem it never saw.
    _wait_for_real_oomkilled(k8s_adapter, TARGET_NAMESPACE, OOM_WAIT_TIMEOUT_S, OOM_POLL_INTERVAL_S)

    body = (
        "Live scenario_07 test event. This PR deliberately lowers memory to 32Mi in "
        "Helm/values-dev.yaml for a Spring Boot service, which triggers OOMKilled. "
        "The agent should diagnose the memory-limit root cause and propose a fix."
    )

    watchdog = AgentWatchdog(adapter=mcp_adapter, graph=build_graph(), monitors=[])
    watchdog._dispatch_inner(_build_pr_event(TARGET_REPO, pr_number, branch, body))
    watchdog._executor.shutdown(wait=True)

    deadline = time.monotonic() + 90
    last_comments_result: dict[str, Any] = {}
    bot_comments: list[dict[str, Any]] = []

    while time.monotonic() < deadline:
        comments_result = gh.get_pr_comments(TARGET_REPO, pr_number)
        last_comments_result = comments_result
        assert comments_result.get("status") == "ok", comments_result
        bot_comments = [
            c for c in comments_result["comments"] if c.get("body", "").startswith("## gh_action_bot")
        ]
        if bot_comments:
            break
        time.sleep(3)

    assert bot_comments, (
        "Expected gh_action_bot to comment on the PR within 90s, but no such comment appeared. "
        f"Last poll result: {last_comments_result}."
    )

    combined = " ".join(c.get("body", "") for c in bot_comments).lower()
    assert "oom" in combined or "memory" in combined
    assert "32mi" in combined or "512mi" in combined or "resources.limits.memory" in combined
    assert "helm" in combined or "values-dev.yaml" in combined
