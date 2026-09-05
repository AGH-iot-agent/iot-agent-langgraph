from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from devops_agent.adapters.github_adapter import GHAdapter
from devops_agent.adapters.k8s_adapter import K8sAdapter
from devops_agent.event import AgentEvent
from devops_agent.graph import build_graph
from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.monitors.github_issues import GitHubIssueMonitor
from devops_agent.watchdog import AgentWatchdog

from tests.integration.test_ci_fix_integration import (
    _assert_runtime_preconditions,
    _uninstall_release_if_exists,
)
from tests.test_scenarios.github_tokens import non_agent_gh_adapter
from tests.test_scenarios.test_scenarios_e2e import _RecordingAdapter

SCENARIO_DIR = Path(__file__).parent


class _GraphWithSecretEscalation:
    """Deterministic stand-in graph for scenario 03 stub assertions."""

    def invoke(self, _state: dict[str, Any]) -> dict[str, Any]:
        return {
            "final_summary": (
                "Investigated the CrashLoopBackOff in iotag-dev. The "
                "db-credentials secret is also missing on sbx, which is used "
                "for validation, so this looks like a broader sbx/dev secret "
                "mismatch rather than a one-off. Admins have been escalated to "
                "investigate and restore the secret in both namespaces."
            )
        }

TARGET_REPO = os.environ.get("TEST_03_CI_FIX_TARGET_REPO")
TARGET_NAMESPACE = os.environ.get("TEST_03_CI_FIX_TARGET_NAMESPACE", "iotag-sbx")
BASE_BRANCH = os.environ.get("TEST_03_CI_FIX_BASE_BRANCH", "main")
KUBECONFIG = os.environ.get("TEST_03_CI_FIX_KUBECONFIG")

_SBX_RELEASE_NAME = "ci-fix-it-test"
_SECRET_NAME = "db-credentials"
_DEV_NAMESPACE = "iotag-dev"

# Loose keywords we expect the agent's investigation/escalation comment to
# surface. Kept loose (rather than a fixed phrase) since this comment is
# produced by the real agent, not a canned string.
_ESCALATION_KEYWORDS = (
    "secret",
    "credential",
    "iotag-sbx",
)


def _require_env() -> None:
    missing = [
        name
        for name, val in {
            "TEST_03_CI_FIX_TARGET_REPO": TARGET_REPO,
        }.items()
        if not val
    ]
    if missing:
        pytest.fail(f"Missing required environment variables: {missing}")


@pytest.fixture(scope="module")
def gh() -> GHAdapter:
    return non_agent_gh_adapter()


@pytest.fixture(scope="module")
def k8s_adapter() -> K8sAdapter:
    return K8sAdapter(kubeconfig=KUBECONFIG)


@pytest.fixture(scope="module")
def mcp_adapter() -> MCPAdapter:
    return MCPAdapter()


@pytest.fixture(scope="module")
def preflight_requirements(k8s_adapter: K8sAdapter) -> None:
    _assert_runtime_preconditions(k8s_adapter)


@pytest.fixture
def sbx_release(k8s_adapter: K8sAdapter) -> Any:
    _uninstall_release_if_exists(k8s_adapter, _SBX_RELEASE_NAME)
    try:
        yield _SBX_RELEASE_NAME
    finally:
        _uninstall_release_if_exists(k8s_adapter, _SBX_RELEASE_NAME)


@pytest.fixture(scope="module")
def remove_secret(k8s_adapter: K8sAdapter) -> Any:
    """Remove db-credentials from sbx so the agent finds the same gap it's
    being asked to investigate in dev, surfacing the sbx/dev mismatch."""
    try:
        result = k8s_adapter.run(
            "core",
            "delete_secret",
            {"name": _SECRET_NAME, "namespace": TARGET_NAMESPACE},
        )
        if result.get("status") != "ok":
            pytest.fail(f"Failed to remove secret: {result}")
        yield result
    finally:
        k8s_adapter.run(
            "core",
            "create_secret",
            {
                "name": _SECRET_NAME,
                "namespace": TARGET_NAMESPACE,
                "data": {
                    "SPRING_DATASOURCE_USERNAME": "c29tZXVzZXI=",
                    "SPRING_DATASOURCE_PASSWORD": "c29tZXBhc3N3b3Jk",
                },
            },
        )


def _build_issue_event(body: str, issue_number: int, title: str) -> AgentEvent:
    return AgentEvent(
        kind="github_issue",
        title=f"{TARGET_REPO}#{issue_number}: {title}",
        body=body,
        context={
            "repo_full_name": TARGET_REPO,
            "issue_number": issue_number,
        },
    )


@pytest.fixture
def live_scenario03_create_issue(gh: GHAdapter) -> Any:
    """Open an issue describing the CrashLoopBackOff caused by the missing
    db-credentials secret in iotag-dev."""
    _require_env()

    issue_body = (SCENARIO_DIR / "issue_body.md").read_text(encoding="utf-8")

    result = gh.create_issue(
        repo=TARGET_REPO,
        title="Scenario 03: iot-agent-authentication CrashLoopBackOff (missing db-credentials secret)",
        body=issue_body,
        labels=["iot-devops-agent", "incident", "k8s"],
    )

    assert result.get("status") == "ok", (
        f"Failed to create issue for scenario 03: {result}"
    )
    issue_number = result["number"]
    info = {
        "issue_number": issue_number,
        "issue_body": issue_body,
    }
    try:
        yield info
    finally:
        gh.update_issue(TARGET_REPO, issue_number, "closed")


@pytest.mark.integration
def test_scenario03_appends_escalation_note_for_issue_comment() -> None:
    issue_body = (SCENARIO_DIR / "issue_body.md").read_text(encoding="utf-8")
    adapter = _RecordingAdapter()
    watchdog = AgentWatchdog(adapter=adapter, graph=_GraphWithSecretEscalation(), monitors=[])

    watchdog._dispatch_inner(_build_issue_event(issue_body, 3, "CrashLoopBackOff"))

    issue_comment_calls = [
        c for c in adapter.calls
        if c["service"] == "github" and c["action"] == "create_issue_comment"
    ]
    assert len(issue_comment_calls) == 1

    comment_body = issue_comment_calls[0]["args"]["body"].lower()
    assert "## gh_action_bot" in comment_body
    assert "secret" in comment_body and "sbx" in comment_body
    assert "validation" in comment_body
    assert "admin" in comment_body and "escalat" in comment_body


@pytest.mark.integration
def test_scenario03_real_agent_comments_on_live_issue(
    live_scenario03_create_issue: dict[str, Any],
    remove_secret: Any,
    gh: GHAdapter,
    mcp_adapter: MCPAdapter,
    agent_mode: str,
) -> None:
    issue_number = live_scenario03_create_issue["issue_number"]
    from tests.test_scenarios.run_metrics import metrics_snapshot, record_live_comment

    started_at = time.time()
    metrics_before = metrics_snapshot()

    monitor = GitHubIssueMonitor(
        adapter=mcp_adapter,
        org=TARGET_REPO.split("/")[0],
    )

    watchdog = AgentWatchdog(
        adapter=mcp_adapter,
        graph=build_graph(agent_mode=agent_mode),
        monitors=[monitor],
    )

    futures = watchdog._tick()
    watchdog._executor.shutdown(wait=True)
    if futures:
        for f in futures:
            f.result()  
            
    bot_comments: list[dict[str, Any]] = []
    deadline = time.monotonic() + 60
    last_comments_result: dict[str, Any] = {}
    while time.monotonic() < deadline:
        comments_result = gh.get_issue_comments(
            repo=TARGET_REPO,
            issue_number=issue_number,
        )
        last_comments_result = comments_result
        assert comments_result.get("status") == "ok", (
            f"Failed to get comments for issue {issue_number}: {comments_result}"
        )
        bot_comments = [
            c for c in comments_result["comments"]
            if c.get("body", "").startswith("## gh_action_bot")
        ]
        if bot_comments:
            break
        time.sleep(3)

    assert bot_comments, (
        "Expected gh_action_bot to comment on the issue within 60s, but no "
        f"such comment appeared. Last poll result: {last_comments_result}. "
        "If this keeps failing with an empty comment list (and no exception "
        "from futures above), check whether GitHubIssueMonitor actually "
        "picked up the newly created issue, and whether mcp_adapter has "
        "write access to comment on it."
    )

    comment = bot_comments[-1].get("body", "")
    from tests.test_scenarios.requirement_asserts import (
        assert_comment_structure,
        assert_ground_truth_diagnosis,
        assert_secret_investigation_comment,
    )

    try:
        assert_comment_structure(comment, require_proposed=False, require_validation=False)
        assert_secret_investigation_comment(comment)
        assert_ground_truth_diagnosis(comment, "03")
        record_live_comment(
            scenario_id="03",
            agent_mode=agent_mode,
            comment_body=comment,
            started_at=started_at,
            metrics_before=metrics_before,
            workflow_ok=True,
            validation_namespace="iotag-sbx",
        )
    except Exception as exc:
        record_live_comment(
            scenario_id="03",
            agent_mode=agent_mode,
            comment_body=comment,
            started_at=started_at,
            metrics_before=metrics_before,
            workflow_ok=False,
            error=str(exc),
            validation_namespace="iotag-sbx",
        )
        raise