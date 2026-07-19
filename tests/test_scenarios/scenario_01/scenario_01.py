from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from devops_agent.event import AgentEvent
from devops_agent.graph import build_graph
from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.watchdog import AgentWatchdog, WatchdogConfig
from devops_agent.monitors.github_issues import GitHubIssueMonitor

# MUST be "owner/repo" — gh / the GitHub REST API needs the fully qualified
# slug. A bare repo name breaks org.split("/")[0] (used to build the org
# for GitHubIssueMonitor) AND every real_github_adapter.run(..., {"repo": ...})
# call, both of which then 404 silently against the wrong resource.
TARGET_REPO = "AGH-iot-agent/iot-agent-login-screen"

SCENARIO_DIR = Path(__file__).parent


class _RecordingAdapter:
    """Passed into AgentWatchdog under test. Records every call instead of
    forwarding it anywhere, so assertions can inspect exactly what the
    watchdog tried to do without touching GitHub."""

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
        return {"status": "ok"}


@pytest.fixture(scope="session")
def preflight_requirements() -> None:
    if not (os.environ.get("TEST_O1_ENV_GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        pytest.fail(
            "A GitHub token is required (TEST_O1_ENV_GH_TOKEN or GITHUB_TOKEN) "
            "to create the live scenario_01 issue."
        )


@pytest.fixture(scope="session")
def real_github_adapter(preflight_requirements: None) -> MCPAdapter:
    """Real MCPAdapter, initialized once for the whole test session."""
    return MCPAdapter()


@pytest.fixture
def live_github_issue(real_github_adapter: MCPAdapter) -> Any:
    """Creates a real GitHub issue mirroring issue_body.md via scenario_01.sh,
    yields the parsed creation response (has "number", "html_url", ...),
    and closes the issue afterward regardless of test outcome.

    scenario_01.sh's stdout contract is exactly one JSON object (the GitHub
    API response) — everything else (curl tracing, error diagnostics) goes
    to stderr. That's the only channel used to get the issue number back
    into this process; env vars set inside the child shell can't propagate
    to the parent pytest process.
    """
    token = os.environ.get("TEST_O1_ENV_GH_TOKEN") or os.environ["GITHUB_TOKEN"]

    script_path = SCENARIO_DIR / "scenario_01.sh"
    result = subprocess.run(
        ["bash", str(script_path), token],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"scenario_01.sh failed (exit {result.returncode}):\n{result.stderr}"
        )

    try:
        issue = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            "scenario_01.sh did not print valid JSON on stdout.\n"
            f"stdout was:\n{result.stdout!r}\n"
            f"stderr was:\n{result.stderr}"
        )

    issue_number = issue["number"]

    try:
        yield issue
    finally:
        close_result = real_github_adapter.run(
            "github",
            "update_issue",
            {
                "repo": TARGET_REPO,
                "issue_number": int(issue_number),
                "state": "closed",
            },
        )
        if close_result.get("status") != "ok":
            # Don't fail the test run over cleanup, but make it loud —
            # leftover open issues from previous runs are exactly what
            # caused the confusing #119 vs #121 mismatch earlier.
            print(
                f"[live_github_issue] WARNING: failed to close issue "
                f"#{issue_number}: {close_result}"
            )


class _GraphWithoutSummary:
    def invoke(self, _state: dict[str, Any]) -> dict[str, Any]:
        return {}


class _GraphWithFixPR:
    def invoke(self, _state: dict[str, Any]) -> dict[str, Any]:
        return {
            "final_summary": "Automated analysis completed.",
            "pr_url": f"https://github.com/{TARGET_REPO}/pull/999",
        }


def _build_issue_event(body: str, issue_number: int) -> AgentEvent:
    return AgentEvent(
        kind="github_issue",
        title=f"{TARGET_REPO}#{issue_number}: npm ci fails with missing package-lock.json",
        body=body,
        context={
            "repo_full_name": TARGET_REPO,
            "issue_number": issue_number,
        },
    )


@pytest.mark.integration
def test_scenario01_real_agent_comments_on_live_issue(
    live_github_issue: dict[str, Any],
    real_github_adapter: MCPAdapter,
) -> None:
    org = TARGET_REPO.split("/")[0]

    monitor = GitHubIssueMonitor(
        adapter=real_github_adapter,
        org=org,
    )

    watchdog = AgentWatchdog(
        adapter=real_github_adapter,
        graph=build_graph(),
        monitors=[monitor],
    )

    watchdog._tick()
    watchdog._executor.shutdown(wait=True)
    issue_number = live_github_issue["number"]

    comments_result = real_github_adapter.run(
        "github",
        "get_issue_comments",
        {
            "repo": TARGET_REPO,
            "issue_number": issue_number,
        },
    )

    assert comments_result["status"] == "ok"

    comments = comments_result["comments"]

    bot_comments = [
        c for c in comments
        if c.get("body", "").startswith("## gh_action_bot")
    ]

    assert bot_comments, (
        "Expected gh_action_bot to comment on the issue, "
        f"but comments were: {comments}"
    )

    comment = bot_comments[0]["body"]

    assert "Root Cause" in comment
    assert "package-lock.json" in comment
    assert "npm ci" in comment
    assert "Proposed Fix" in comment
    assert "npm install" in comment
    assert "Steps to Resolve" in comment
    assert "Risk / Side Effects" in comment


@pytest.mark.integration
def test_scenario01_appends_fix_pr_link_for_issue_comment(
    live_github_issue: dict[str, Any],
) -> None:
    issue_body = (SCENARIO_DIR / "issue_body.md").read_text(encoding="utf-8")
    adapter = _RecordingAdapter()
    watchdog = AgentWatchdog(adapter=adapter, graph=_GraphWithFixPR(), monitors=[])

    watchdog._dispatch_inner(_build_issue_event(issue_body, live_github_issue["number"]))

    issue_comment_calls = [
        c for c in adapter.calls
        if c["service"] == "github" and c["action"] == "create_issue_comment"
    ]
    assert len(issue_comment_calls) == 1

    comment_body = issue_comment_calls[0]["args"]["body"]
    assert "## gh_action_bot" in comment_body
    assert "Agent created a fix PR" in comment_body
    assert "/pull/999" in comment_body


@pytest.mark.integration
def test_scenario01_posts_issue_related_information_when_summary_missing(
    live_github_issue: dict[str, Any],
) -> None:
    issue_body = (SCENARIO_DIR / "issue_body.md").read_text(encoding="utf-8")
    adapter = _RecordingAdapter()
    watchdog = AgentWatchdog(adapter=adapter, graph=_GraphWithoutSummary(), monitors=[])

    watchdog._dispatch_inner(_build_issue_event(issue_body, live_github_issue["number"]))

    issue_comment_calls = [
        c for c in adapter.calls
        if c["service"] == "github" and c["action"] == "create_issue_comment"
    ]
    assert len(issue_comment_calls) == 1

    comment_body = issue_comment_calls[0]["args"]["body"]
    assert "## gh_action_bot" in comment_body
    assert "[GITHUB_ISSUE]" in comment_body
    assert "package-lock.json" in comment_body
    assert "ENOENT" in comment_body


@pytest.mark.integration
def test_scenario01_tick_detects_live_issue_via_real_monitor_and_comments(
    live_github_issue: dict[str, Any],
    real_github_adapter: MCPAdapter,
) -> None:
    """End-to-end minus the background thread: a REAL GitHubIssueMonitor
    polls REAL GitHub (list_org_repos -> list_issues -> get_issue_comments),
    finds the live-created issue via its "iot-devops-agent" label, builds
    the AgentEvent itself, and the watchdog dispatches it - all through
    _tick(), not _dispatch_inner() directly.
    """
    org = TARGET_REPO.split("/")[0]
    monitor = GitHubIssueMonitor(adapter=real_github_adapter, org=org)

    write_adapter = _RecordingAdapter()
    watchdog = AgentWatchdog(adapter=write_adapter, graph=build_graph(), monitors=[monitor])

    watchdog._tick()
    watchdog._executor.shutdown(wait=True)

    issue_comment_calls = [
        c for c in write_adapter.calls
        if c["service"] == "github" and c["action"] == "create_issue_comment"
    ]
    matching = [
        c for c in issue_comment_calls
        if c["args"].get("repo") == TARGET_REPO
        and c["args"].get("issue_number") == live_github_issue["number"]
    ]
    assert matching, (
        f"Expected a create_issue_comment call for {TARGET_REPO}#{live_github_issue['number']}, "
        f"but recorded calls were: {issue_comment_calls}"
    )
    assert "## gh_action_bot" in matching[0]["args"]["body"]