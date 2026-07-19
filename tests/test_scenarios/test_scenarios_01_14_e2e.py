from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable

import pytest

from devops_agent.event import AgentEvent
from devops_agent.graph import build_graph
from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.monitors.disk_pressure import DiskPressureMonitor
from devops_agent.monitors.github_issues import GitHubIssueMonitor
from devops_agent.monitors.github_pr_build_monitor import GitHubPRBuildMonitor
from devops_agent.monitors.prometheus_metrics import PrometheusMetricsMonitor
from devops_agent.monitors.resource_quota import ResourceQuotaMonitor
from devops_agent.watchdog import AgentWatchdog

SCENARIOS_DIR = Path(__file__).resolve().parent

_GITHUB_WRITE_TOOLS = frozenset(
    {
        "create_pull_request",
        "create_branch",
        "commit_file",
        "create_issue_comment",
    }
)


class _RecordingAdapter:
    """Records outgoing write calls from watchdog for deterministic assertions."""

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
        if service == "github" and action in _GITHUB_WRITE_TOOLS:
            if action == "create_issue_comment":
                return {"status": "ok", "id": 1}
            return {"status": "ok", "pr_url": "https://example.org/fix"}
        return {"status": "ok"}


class _GraphWithoutSummary:
    def invoke(self, _state: dict[str, Any]) -> dict[str, Any]:
        return {}


class _GraphWithFixPR:
    def __init__(self, repo: str) -> None:
        self._repo = repo

    def invoke(self, _state: dict[str, Any]) -> dict[str, Any]:
        return {
            "final_summary": "Automated analysis completed.",
            "pr_url": f"https://github.com/{self._repo}/pull/999",
        }

ISSUE_SCENARIO_SPECS: dict[str, dict[str, Any]] = {
    "01": {
        "repo": "AGH-iot-agent/iot-agent-login-screen",
        "script": "scenario_01.sh",
        "title_fragment": "npm ci",
        "expected_keywords": ("package-lock.json", "enoent"),
    },
    "04": {
        "repo": "AGH-iot-agent/iot-agent-device-api",
        "script": "scenario_04.sh",
        "title_fragment": "Maven BUILD FAILURE",
        "expected_keywords": ("root cause", "2.1.0", "2.0.1", "pom.xml"),
    },
    "06": {
        "repo": "AGH-iot-agent/iot-agent-logs",
        "script": "scenario_06.sh",
        "title_fragment": "env' context not available",
        "expected_keywords": ("root cause", "with", "needs", "outputs"),
    },
    "08": {
        "repo": "AGH-iot-agent/iot-agent-authentication",
        "script": "scenario_08.sh",
        "title_fragment": "CrashLoopBackOff",
        "expected_keywords": ("root cause", "localhost", "postgres", "iotag-dev"),
    },
    "09": {
        "repo": "AGH-iot-agent/iot-agent-authentication",
        "script": "scenario_09.sh",
        "title_fragment": "Orphaned ConfigMaps",
        "expected_keywords": ("root cause", "orphaned", "kubectl delete"),
    },
}

PR_SCENARIO_SPECS: dict[str, dict[str, str]] = {
    "02": {
        "repo": "AGH-iot-agent/iot-agent-login-screen",
        "script": "scenario_02.sh",
        "branch_prefix": "test/scenario_02_",
    },
    "05": {
        "repo": "AGH-iot-agent/iot-agent-alert-api",
        "script": "scenario_05.sh",
        "branch_prefix": "test/scenario_05_",
    },
    "07": {
        "repo": "AGH-iot-agent/iot-agent-stream-worker",
        "script": "scenario_07.sh",
        "branch_prefix": "test/scenario_07_",
    },
    "10": {
        "repo": "AGH-iot-agent/iot-agent-logs",
        "script": "scenario_10.sh",
        "branch_prefix": "test/scenario_10_",
    },
    "12": {
        "repo": "AGH-iot-agent/iot-agent-dashboard-api",
        "script": "scenario_12.sh",
        "branch_prefix": "test/scenario_12_",
    },
}

MONITOR_SCENARIO_SPECS: dict[str, dict[str, str]] = {
    "03": {
        "script": "scenario_03.sh",
        "cleanup_script": "cleanup_03.sh",
        "expected_event_kind": "request_rate_spike",
        "monitor_kind": "prometheus",
    },
    "11": {
        "script": "scenario_11.sh",
        "cleanup_script": "cleanup_11.sh",
        "expected_event_kind": "high_http_latency",
        "monitor_kind": "prometheus",
    },
    "13": {
        "script": "scenario_13.sh",
        "cleanup_script": "cleanup_13.sh",
        "expected_event_kind": "namespace_quota_exceeded",
        "monitor_kind": "resource_quota",
    },
    "14": {
        "script": "scenario_14.sh",
        "cleanup_script": "cleanup_14.sh",
        "expected_event_kind": "pvc_disk_pressure",
        "monitor_kind": "disk_pressure",
    },
}


@pytest.fixture(scope="session")
def scenario_wait_settings() -> dict[str, int]:
    return {
        "issue_comment_timeout_s": int(os.environ.get("TEST_SCENARIO_ISSUE_WAIT_S", "420")),
        "pr_comment_timeout_s": int(os.environ.get("TEST_SCENARIO_PR_WAIT_S", "900")),
        "monitor_alert_timeout_s": int(os.environ.get("TEST_SCENARIO_MONITOR_WAIT_S", "420")),
        "tick_interval_s": int(os.environ.get("TEST_SCENARIO_TICK_INTERVAL_S", "25")),
        "script_timeout_s": int(os.environ.get("TEST_SCENARIO_SCRIPT_TIMEOUT_S", "900")),
        "max_watchdog_ticks": int(os.environ.get("TEST_SCENARIO_MAX_WATCHDOG_TICKS", "6")),
    }


@pytest.fixture(scope="session")
def e2e_preflight() -> None:
    if not _is_truthy(os.environ.get("RUN_SCENARIO_E2E")):
        pytest.skip("Set RUN_SCENARIO_E2E=1 to run full scenario E2E suite")

    missing = [
        var
        for var in ("OPENAI_API_KEY", "GH_TOKEN")
        if not os.environ.get(var)
    ]
    if missing:
        pytest.skip(f"Missing required env for E2E: {', '.join(missing)}")

    for cmd in ("gh", "git", "kubectl", "curl", "jq"):
        if not _has_command(cmd):
            pytest.skip(f"Missing required command in PATH: {cmd}")


@pytest.fixture(scope="session")
def real_github_adapter(e2e_preflight: None) -> MCPAdapter:
    return MCPAdapter()


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _has_command(name: str) -> bool:
    return subprocess.run(["bash", "-lc", f"command -v {name}"], capture_output=True).returncode == 0


def _run_script(script_path: Path, timeout_s: int, args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    if not script_path.exists():
        raise FileNotFoundError(f"Missing script: {script_path}")

    cmd = ["bash", script_path.name]
    if args:
        cmd.extend(args)

    return subprocess.run(
        cmd,
        cwd=script_path.parent,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )


def _list_open_issue_numbers(adapter: MCPAdapter, repo: str) -> set[int]:
    response = adapter.run("github", "list_issues", {"repo": repo, "state": "open"})
    if response.get("status") != "ok":
        return set()
    return {
        int(issue["number"])
        for issue in response.get("issues", [])
        if isinstance(issue, dict) and issue.get("pull_request") is None and issue.get("number") is not None
    }


def _list_open_prs(adapter: MCPAdapter, repo: str) -> list[dict[str, Any]]:
    response = adapter.run("github", "list_pull_requests", {"repo": repo, "state": "open"})
    if response.get("status") != "ok":
        return []
    return [pr for pr in response.get("pull_requests", []) if isinstance(pr, dict)]


def _wait_until(
    condition: Callable[[], bool],
    timeout_s: int,
    interval_s: int,
    description: str,
) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if condition():
            return
        time.sleep(interval_s)
    pytest.fail(f"Timeout waiting for: {description}")


def _find_new_issue_number(adapter: MCPAdapter, repo: str, before: set[int], title_fragment: str) -> int:
    def _fetch() -> int | None:
        response = adapter.run("github", "list_issues", {"repo": repo, "state": "open"})
        if response.get("status") != "ok":
            return None
        for issue in response.get("issues", []):
            if issue.get("pull_request") is not None:
                continue
            number = int(issue.get("number", 0))
            title = str(issue.get("title", ""))
            if number not in before and title_fragment.lower() in title.lower():
                return number
        return None

    found: dict[str, int] = {}

    def _cond() -> bool:
        number = _fetch()
        if number is None:
            return False
        found["number"] = number
        return True

    _wait_until(_cond, timeout_s=120, interval_s=5, description=f"new issue in {repo}")
    return found["number"]


def _find_new_pr_number(adapter: MCPAdapter, repo: str, before: set[int], branch_prefix: str) -> int:
    def _fetch() -> int | None:
        prs = _list_open_prs(adapter, repo)
        for pr in prs:
            number = int(pr.get("number", 0))
            head_ref = str((pr.get("head") or {}).get("ref", ""))
            if number not in before and head_ref.startswith(branch_prefix):
                return number
        return None

    found: dict[str, int] = {}

    def _cond() -> bool:
        number = _fetch()
        if number is None:
            return False
        found["number"] = number
        return True

    _wait_until(_cond, timeout_s=180, interval_s=6, description=f"new PR in {repo}")
    return found["number"]


def _issue_has_bot_comment_with_keywords(
    adapter: MCPAdapter,
    repo: str,
    issue_number: int,
    expected_keywords: tuple[str, ...],
) -> bool:
    result = adapter.run(
        "github",
        "get_issue_comments",
        {
            "repo": repo,
            "issue_number": issue_number,
        },
    )
    if result.get("status") != "ok":
        return False

    for comment in result.get("comments", []):
        body = str(comment.get("body", ""))
        lower_body = body.lower()
        if not body.startswith("## gh_action_bot"):
            continue
        if all(keyword.lower() in lower_body for keyword in expected_keywords):
            return True
    return False


def _pr_has_bot_comment(adapter: MCPAdapter, repo: str, pr_number: int) -> bool:
    result = adapter.run(
        "github",
        "get_pr_comments",
        {
            "repo": repo,
            "pr_number": pr_number,
        },
    )
    if result.get("status") != "ok":
        return False

    for comment in result.get("comments", []):
        body = str(comment.get("body", ""))
        if body.startswith("## gh_action_bot"):
            return True
    return False


def _get_issue_bot_comment_body(adapter: MCPAdapter, repo: str, issue_number: int) -> str | None:
    result = adapter.run(
        "github",
        "get_issue_comments",
        {
            "repo": repo,
            "issue_number": issue_number,
        },
    )
    if result.get("status") != "ok":
        return None

    for comment in result.get("comments", []):
        body = str(comment.get("body", ""))
        if body.startswith("## gh_action_bot"):
            return body
    return None


def _get_pr_bot_comment_body(adapter: MCPAdapter, repo: str, pr_number: int) -> str | None:
    result = adapter.run(
        "github",
        "get_pr_comments",
        {
            "repo": repo,
            "pr_number": pr_number,
        },
    )
    if result.get("status") != "ok":
        return None

    for comment in result.get("comments", []):
        body = str(comment.get("body", ""))
        if body.startswith("## gh_action_bot"):
            return body
    return None


def _close_issue(adapter: MCPAdapter, repo: str, issue_number: int) -> None:
    adapter.run(
        "github",
        "update_issue",
        {
            "repo": repo,
            "issue_number": issue_number,
            "state": "closed",
        },
    )


def _close_pr(repo: str, pr_number: int) -> None:
    subprocess.run(
        ["gh", "pr", "close", str(pr_number), "--repo", repo, "--delete-branch"],
        text=True,
        capture_output=True,
        check=False,
    )


def _monitor_for_kind(kind: str, adapter: MCPAdapter):
    namespace = os.environ.get("KUBERNETES_NAMESPACE", "iotag-dev")
    if kind == "prometheus":
        return PrometheusMetricsMonitor(adapter=adapter, namespace=namespace)
    if kind == "resource_quota":
        return ResourceQuotaMonitor(adapter=adapter, namespace=namespace)
    if kind == "disk_pressure":
        return DiskPressureMonitor(adapter=adapter, namespace=namespace)
    raise ValueError(f"Unsupported monitor kind: {kind}")


def _run_watchdog_until(
    watchdog: AgentWatchdog,
    condition: Callable[[], bool],
    timeout_s: int,
    tick_interval_s: int,
    max_ticks: int,
    description: str,
) -> None:
    deadline = time.time() + timeout_s
    ticks_done = 0
    try:
        while time.time() < deadline:
            if ticks_done >= max_ticks:
                pytest.fail(
                    f"Exceeded watchdog tick budget ({max_ticks}) while waiting for: {description}"
                )
            watchdog._tick()
            ticks_done += 1
            if condition():
                return
            time.sleep(tick_interval_s)
    finally:
        watchdog._executor.shutdown(wait=True)

    pytest.fail(f"Timeout waiting for watchdog condition: {description}")


def _build_issue_event(repo: str, issue_number: int, body: str) -> AgentEvent:
    return AgentEvent(
        kind="github_issue",
        title=f"{repo}#{issue_number}: npm ci fails with missing package-lock.json",
        body=body,
        context={
            "repo_full_name": repo,
            "issue_number": issue_number,
        },
    )


@pytest.mark.integration
def test_scenario01_posts_issue_related_information_when_summary_missing() -> None:
    issue_body_path = SCENARIOS_DIR / "scenario_01" / "issue_body.md"
    issue_body = issue_body_path.read_text(encoding="utf-8")

    repo = os.environ.get("TEST_SCENARIO01_TARGET_REPO", "AGH-iot-agent/iot-agent-login-screen")
    adapter = _RecordingAdapter()
    watchdog = AgentWatchdog(adapter=adapter, graph=_GraphWithoutSummary(), monitors=[])

    watchdog._dispatch_inner(_build_issue_event(repo, issue_number=1, body=issue_body))

    issue_comment_calls = [
        c
        for c in adapter.calls
        if c["service"] == "github" and c["action"] == "create_issue_comment"
    ]
    assert len(issue_comment_calls) == 1

    comment_body = issue_comment_calls[0]["args"]["body"]
    assert "## gh_action_bot" in comment_body
    assert "[GITHUB_ISSUE]" in comment_body
    assert "package-lock.json" in comment_body
    assert "ENOENT" in comment_body


@pytest.mark.integration
def test_scenario01_appends_fix_pr_link_for_issue_comment() -> None:
    issue_body_path = SCENARIOS_DIR / "scenario_01" / "issue_body.md"
    issue_body = issue_body_path.read_text(encoding="utf-8")

    repo = os.environ.get("TEST_SCENARIO01_TARGET_REPO", "AGH-iot-agent/iot-agent-login-screen")
    adapter = _RecordingAdapter()
    watchdog = AgentWatchdog(adapter=adapter, graph=_GraphWithFixPR(repo), monitors=[])

    watchdog._dispatch_inner(_build_issue_event(repo, issue_number=2, body=issue_body))

    issue_comment_calls = [
        c
        for c in adapter.calls
        if c["service"] == "github" and c["action"] == "create_issue_comment"
    ]
    assert len(issue_comment_calls) == 1

    comment_body = issue_comment_calls[0]["args"]["body"]
    assert "## gh_action_bot" in comment_body
    assert "Agent created a fix PR" in comment_body
    assert "/pull/999" in comment_body


def test_find_new_issue_number_skips_pr_and_matches_title() -> None:
    class _Adapter:
        def run(self, _service: str, _action: str, _args: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "ok",
                "issues": [
                    {
                        "number": 50,
                        "title": "Unrelated",
                        "pull_request": None,
                    },
                    {
                        "number": 51,
                        "title": "Build failure",
                        "pull_request": {"url": "https://api.github.com/pr/51"},
                    },
                    {
                        "number": 52,
                        "title": "Build failure in pipeline",
                        "pull_request": None,
                    },
                ],
            }

    number = _find_new_issue_number(
        _Adapter(),
        repo="AGH-iot-agent/iot-agent-login-screen",
        before={50},
        title_fragment="build failure",
    )
    assert number == 52


def test_find_new_pr_number_filters_by_branch_prefix() -> None:
    class _Adapter:
        def run(self, _service: str, _action: str, _args: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "ok",
                "pull_requests": [
                    {"number": 10, "head": {"ref": "feature/abc"}},
                    {"number": 11, "head": {"ref": "test/scenario_07_foo"}},
                    {"number": 12, "head": {"ref": "test/scenario_07_bar"}},
                ],
            }

    number = _find_new_pr_number(
        adapter=_Adapter(),
        repo="AGH-iot-agent/iot-agent-stream-worker",
        before={11},
        branch_prefix="test/scenario_07_",
    )
    assert number == 12


def test_issue_has_bot_comment_with_keywords_requires_prefix_and_all_keywords() -> None:
    class _Adapter:
        def run(self, _service: str, _action: str, _args: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "ok",
                "comments": [
                    {"body": "Regular comment with package-lock.json and ENOENT"},
                    {"body": "## gh_action_bot Found package-lock.json only"},
                    {"body": "## gh_action_bot Found package-lock.json and ENOENT"},
                ],
            }

    assert _issue_has_bot_comment_with_keywords(
        _Adapter(),
        repo="AGH-iot-agent/iot-agent-login-screen",
        issue_number=1,
        expected_keywords=("package-lock.json", "ENOENT"),
    )


def test_wait_until_raises_pytest_fail_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    timeline = iter([0.0, 0.1, 0.2])
    monkeypatch.setattr(time, "time", lambda: next(timeline))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with pytest.raises(pytest.fail.Exception, match="Timeout waiting for: synthetic condition"):
        _wait_until(
            condition=lambda: False,
            timeout_s=0,
            interval_s=0,
            description="synthetic condition",
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("", False),
        (None, False),
    ],
)
def test_is_truthy_covers_common_inputs(raw: str | None, expected: bool) -> None:
    assert _is_truthy(raw) is expected


def test_run_script_raises_when_script_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sh"
    with pytest.raises(FileNotFoundError, match="Missing script"):
        _run_script(missing, timeout_s=1)


def test_list_open_issue_numbers_handles_api_error() -> None:
    class _Adapter:
        def run(self, _service: str, _action: str, _args: dict[str, Any]) -> dict[str, Any]:
            return {"status": "error", "message": "boom"}

    assert _list_open_issue_numbers(_Adapter(), "AGH-iot-agent/iot-agent-login-screen") == set()


def test_list_open_issue_numbers_filters_pull_requests() -> None:
    class _Adapter:
        def run(self, _service: str, _action: str, _args: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "ok",
                "issues": [
                    {"number": 1, "pull_request": None},
                    {"number": 2, "pull_request": {"url": "https://api.github.com/pr/2"}},
                    {"number": "3", "pull_request": None},
                ],
            }

    numbers = _list_open_issue_numbers(_Adapter(), "AGH-iot-agent/iot-agent-login-screen")
    assert numbers == {1, 3}


def test_list_open_prs_handles_api_error_and_non_dict_entries() -> None:
    class _AdapterError:
        def run(self, _service: str, _action: str, _args: dict[str, Any]) -> dict[str, Any]:
            return {"status": "error"}

    class _AdapterMixed:
        def run(self, _service: str, _action: str, _args: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "ok",
                "pull_requests": [
                    {"number": 10},
                    "invalid",
                    42,
                    {"number": 11},
                ],
            }

    assert _list_open_prs(_AdapterError(), "AGH-iot-agent/iot-agent-stream-worker") == []
    assert _list_open_prs(_AdapterMixed(), "AGH-iot-agent/iot-agent-stream-worker") == [
        {"number": 10},
        {"number": 11},
    ]


def test_pr_has_bot_comment_requires_marker_prefix() -> None:
    class _Adapter:
        def run(self, _service: str, _action: str, _args: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "ok",
                "comments": [
                    {"body": "Regular comment"},
                    {"body": "## gh_action_bot Build failed details"},
                ],
            }

    assert _pr_has_bot_comment(
        _Adapter(),
        repo="AGH-iot-agent/iot-agent-stream-worker",
        pr_number=10,
    )


def test_monitor_for_kind_raises_for_unsupported_kind() -> None:
    with pytest.raises(ValueError, match="Unsupported monitor kind"):
        _monitor_for_kind("unknown-kind", adapter=object())


@pytest.mark.integration
def _assert_issue_scenario_comment(
    scenario_id: str,
    spec: dict[str, Any],
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
    script_args: list[str] | None = None,
) -> dict[str, Any]:
    scenario_dir = SCENARIOS_DIR / f"scenario_{scenario_id}"
    script_path = scenario_dir / str(spec["script"])
    repo = str(spec["repo"])
    title_fragment = str(spec["title_fragment"])
    expected_keywords = tuple(str(v) for v in spec["expected_keywords"])

    before_issues = _list_open_issue_numbers(real_github_adapter, repo)

    create_result = _run_script(
        script_path,
        timeout_s=scenario_wait_settings["script_timeout_s"],
        args=script_args,
    )
    assert create_result.returncode == 0, (
        f"scenario_{scenario_id} setup script failed\n"
        f"stdout:\n{create_result.stdout}\n\n"
        f"stderr:\n{create_result.stderr}"
    )

    issue_number = _find_new_issue_number(
        real_github_adapter,
        repo,
        before=before_issues,
        title_fragment=title_fragment,
    )
    assert issue_number > 0

    org = repo.split("/")[0]
    monitor = GitHubIssueMonitor(adapter=real_github_adapter, org=org)
    watchdog = AgentWatchdog(
        adapter=real_github_adapter,
        graph=build_graph(),
        monitors=[monitor],
    )

    try:
        _run_watchdog_until(
            watchdog,
            condition=lambda: _issue_has_bot_comment_with_keywords(
                real_github_adapter,
                repo,
                issue_number,
                expected_keywords,
            ),
            timeout_s=scenario_wait_settings["issue_comment_timeout_s"],
            tick_interval_s=scenario_wait_settings["tick_interval_s"],
            max_ticks=scenario_wait_settings["max_watchdog_ticks"],
            description=f"scenario_{scenario_id} bot comment",
        )
        comment_body = _get_issue_bot_comment_body(real_github_adapter, repo, issue_number)
        assert comment_body is not None, (
            f"scenario_{scenario_id}: expected bot issue comment body after watchdog success"
        )
        return {
            "issue_number": issue_number,
            "comment_body": comment_body,
            "repo": repo,
            "expected_keywords": expected_keywords,
        }
    finally:
        _close_issue(real_github_adapter, repo, issue_number)


def _assert_pr_scenario_comment(
    scenario_id: str,
    spec: dict[str, str],
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> dict[str, Any]:
    scenario_dir = SCENARIOS_DIR / f"scenario_{scenario_id}"
    script_path = scenario_dir / spec["script"]
    repo = spec["repo"]
    branch_prefix = spec["branch_prefix"]

    before_prs = {int(pr.get("number")) for pr in _list_open_prs(real_github_adapter, repo)}

    create_result = _run_script(script_path, timeout_s=scenario_wait_settings["script_timeout_s"])
    assert create_result.returncode == 0, (
        f"scenario_{scenario_id} setup script failed\n"
        f"stdout:\n{create_result.stdout}\n\n"
        f"stderr:\n{create_result.stderr}"
    )

    pr_number = _find_new_pr_number(
        real_github_adapter,
        repo,
        before=before_prs,
        branch_prefix=branch_prefix,
    )
    assert pr_number > 0

    org = repo.split("/")[0]
    monitor = GitHubPRBuildMonitor(adapter=real_github_adapter, org=org)
    watchdog = AgentWatchdog(
        adapter=real_github_adapter,
        graph=build_graph(),
        monitors=[monitor],
    )

    try:
        _run_watchdog_until(
            watchdog,
            condition=lambda: _pr_has_bot_comment(real_github_adapter, repo, pr_number),
            timeout_s=scenario_wait_settings["pr_comment_timeout_s"],
            tick_interval_s=scenario_wait_settings["tick_interval_s"],
            max_ticks=scenario_wait_settings["max_watchdog_ticks"],
            description=f"scenario_{scenario_id} bot PR comment",
        )
        comment_body = _get_pr_bot_comment_body(real_github_adapter, repo, pr_number)
        assert comment_body is not None, (
            f"scenario_{scenario_id}: expected bot PR comment body after watchdog success"
        )
        return {
            "pr_number": pr_number,
            "comment_body": comment_body,
            "repo": repo,
            "branch_prefix": branch_prefix,
        }
    finally:
        _close_pr(repo, pr_number)


def _assert_monitor_scenario_alert(
    scenario_id: str,
    spec: dict[str, str],
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> dict[str, Any]:
    scenario_dir = SCENARIOS_DIR / f"scenario_{scenario_id}"
    script_path = scenario_dir / spec["script"]
    cleanup_path = scenario_dir / spec["cleanup_script"]
    expected_event_kind = spec["expected_event_kind"]

    create_result = _run_script(script_path, timeout_s=scenario_wait_settings["script_timeout_s"])
    assert create_result.returncode == 0, (
        f"scenario_{scenario_id} setup script failed\n"
        f"stdout:\n{create_result.stdout}\n\n"
        f"stderr:\n{create_result.stderr}"
    )

    monitor = _monitor_for_kind(spec["monitor_kind"], real_github_adapter)
    watchdog = AgentWatchdog(
        adapter=real_github_adapter,
        graph=build_graph(),
        monitors=[monitor],
    )

    def _alert_seen() -> bool:
        alerts = watchdog.recent_alerts(limit=100)
        return any(a.get("kind") == expected_event_kind for a in alerts)

    try:
        _run_watchdog_until(
            watchdog,
            condition=_alert_seen,
            timeout_s=scenario_wait_settings["monitor_alert_timeout_s"],
            tick_interval_s=scenario_wait_settings["tick_interval_s"],
            max_ticks=scenario_wait_settings["max_watchdog_ticks"],
            description=f"scenario_{scenario_id} alert {expected_event_kind}",
        )
        recent_alerts = watchdog.recent_alerts(limit=100)
        matched_alert = next((a for a in recent_alerts if a.get("kind") == expected_event_kind), None)
        assert matched_alert is not None, (
            f"scenario_{scenario_id}: watchdog reported success but no matching alert persisted"
        )
        return {
            "expected_event_kind": expected_event_kind,
            "matched_alert": matched_alert,
        }
    finally:
        cleanup_result = _run_script(cleanup_path, timeout_s=180)
        assert cleanup_result.returncode == 0, (
            f"scenario_{scenario_id} cleanup failed\n"
            f"stdout:\n{cleanup_result.stdout}\n\n"
            f"stderr:\n{cleanup_result.stderr}"
        )


@pytest.mark.integration
def test_scenario01_creates_issue_and_bot_comment(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> None:
    spec = ISSUE_SCENARIO_SPECS["01"]
    gh_token = os.environ["GH_TOKEN"]
    _assert_issue_scenario_comment(
        scenario_id="01",
        spec=spec,
        real_github_adapter=real_github_adapter,
        scenario_wait_settings=scenario_wait_settings,
        script_args=[gh_token],
    )


@pytest.mark.integration
def test_scenario04_creates_issue_and_bot_comment(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> None:
    _assert_issue_scenario_comment("04", ISSUE_SCENARIO_SPECS["04"], real_github_adapter, scenario_wait_settings)


@pytest.mark.integration
def test_scenario06_creates_issue_and_bot_comment(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> None:
    _assert_issue_scenario_comment("06", ISSUE_SCENARIO_SPECS["06"], real_github_adapter, scenario_wait_settings)


@pytest.mark.integration
def test_scenario08_creates_issue_and_bot_comment(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> None:
    _assert_issue_scenario_comment("08", ISSUE_SCENARIO_SPECS["08"], real_github_adapter, scenario_wait_settings)


@pytest.mark.integration
def test_scenario09_creates_issue_and_bot_comment(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> None:
    result = _assert_issue_scenario_comment(
        "09",
        ISSUE_SCENARIO_SPECS["09"],
        real_github_adapter,
        scenario_wait_settings,
    )
    comment_body = str(result["comment_body"])
    assert "## gh_action_bot" in comment_body
    assert "[GITHUB_ISSUE]" in comment_body
    assert "orphaned" in comment_body.lower()
    assert "kubectl delete" in comment_body.lower()


@pytest.mark.integration
def test_scenario02_creates_failed_pr_and_bot_comment(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> None:
    _assert_pr_scenario_comment("02", PR_SCENARIO_SPECS["02"], real_github_adapter, scenario_wait_settings)


@pytest.mark.integration
def test_scenario05_creates_failed_pr_and_bot_comment(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> None:
    _assert_pr_scenario_comment("05", PR_SCENARIO_SPECS["05"], real_github_adapter, scenario_wait_settings)


@pytest.mark.integration
def test_scenario07_creates_failed_pr_and_bot_comment(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> None:
    _assert_pr_scenario_comment("07", PR_SCENARIO_SPECS["07"], real_github_adapter, scenario_wait_settings)


@pytest.mark.integration
def test_scenario10_creates_failed_pr_and_bot_comment(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> None:
    result = _assert_pr_scenario_comment(
        "10",
        PR_SCENARIO_SPECS["10"],
        real_github_adapter,
        scenario_wait_settings,
    )
    comment_body = str(result["comment_body"])
    assert "## gh_action_bot" in comment_body
    assert "[GITHUB_PR]" in comment_body
    assert "root cause" in comment_body.lower()


@pytest.mark.integration
def test_scenario12_creates_failed_pr_and_bot_comment(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> None:
    result = _assert_pr_scenario_comment(
        "12",
        PR_SCENARIO_SPECS["12"],
        real_github_adapter,
        scenario_wait_settings,
    )
    comment_body = str(result["comment_body"])
    assert "## gh_action_bot" in comment_body
    assert "[GITHUB_PR]" in comment_body
    assert "root cause" in comment_body.lower()


@pytest.mark.integration
def test_scenario03_emits_request_rate_spike_alert(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> None:
    _assert_monitor_scenario_alert("03", MONITOR_SCENARIO_SPECS["03"], real_github_adapter, scenario_wait_settings)


@pytest.mark.integration
def test_scenario11_emits_high_http_latency_alert(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> None:
    result = _assert_monitor_scenario_alert(
        "11",
        MONITOR_SCENARIO_SPECS["11"],
        real_github_adapter,
        scenario_wait_settings,
    )
    matched_alert = dict(result["matched_alert"])
    assert matched_alert.get("kind") == "high_http_latency"
    assert matched_alert.get("source") in {"prometheus", "monitor", None}


@pytest.mark.integration
def test_scenario13_emits_namespace_quota_exceeded_alert(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> None:
    result = _assert_monitor_scenario_alert(
        "13",
        MONITOR_SCENARIO_SPECS["13"],
        real_github_adapter,
        scenario_wait_settings,
    )
    matched_alert = dict(result["matched_alert"])
    assert matched_alert.get("kind") == "namespace_quota_exceeded"


@pytest.mark.integration
def test_scenario14_emits_pvc_disk_pressure_alert(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
) -> None:
    result = _assert_monitor_scenario_alert(
        "14",
        MONITOR_SCENARIO_SPECS["14"],
        real_github_adapter,
        scenario_wait_settings,
    )
    matched_alert = dict(result["matched_alert"])
    assert matched_alert.get("kind") == "pvc_disk_pressure"
