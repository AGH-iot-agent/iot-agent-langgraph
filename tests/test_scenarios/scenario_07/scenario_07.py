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
from tests.test_scenarios.github_tokens import non_agent_gh_adapter

EXPECTED_SCENARIO07_REPO = "AGH-iot-agent/iot-agent-login-screen"
TARGET_REPO = os.environ.get("TEST_SCENARIO07_TARGET_REPO", EXPECTED_SCENARIO07_REPO)
BASE_BRANCH = os.environ.get("TEST_SCENARIO07_BASE_BRANCH", "main")
TARGET_NAMESPACE = os.environ.get("TEST_SCENARIO07_TARGET_NAMESPACE", "iotag-sbx")
KUBECONFIG = os.environ.get("TEST_SCENARIO07_KUBECONFIG")
OOM_WAIT_TIMEOUT_S = int(os.environ.get("TEST_SCENARIO07_OOM_WAIT_TIMEOUT_S", "600"))
OOM_POLL_INTERVAL_S = int(os.environ.get("TEST_SCENARIO07_OOM_POLL_INTERVAL_S", "15"))
TARGET_DEPLOYMENT = os.environ.get("TEST_SCENARIO07_TARGET_DEPLOYMENT", TARGET_REPO.rsplit("/", 1)[-1])
ALLOW_NON_OOM_TARGET = os.environ.get("TEST_SCENARIO07_ALLOW_NON_OOM_TARGET", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
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
    if TARGET_REPO != EXPECTED_SCENARIO07_REPO and not ALLOW_NON_OOM_TARGET:
        pytest.fail(
            "Scenario 07 expects OOM target repo "
            f"'{EXPECTED_SCENARIO07_REPO}', but got '{TARGET_REPO}'. "
            "Set TEST_SCENARIO07_ALLOW_NON_OOM_TARGET=1 only if you intentionally want another service."
        )


CI_WAIT_TIMEOUT_S = int(os.environ.get("TEST_SCENARIO07_CI_WAIT_TIMEOUT_S", "900"))
CI_POLL_INTERVAL_S = int(os.environ.get("TEST_SCENARIO07_CI_POLL_INTERVAL_S", "15"))


def _wait_for_ci_terminal(gh: GHAdapter, repo: str, branch: str, timeout_s: int, poll_s: int) -> dict[str, Any]:
    """Block until a workflow run for ``branch`` completes (success or failure)."""
    deadline = time.time() + timeout_s
    last_seen: dict[int, str] = {}
    while time.time() < deadline:
        runs_resp = gh.get_workflow_runs(repo, per_page=30)
        if runs_resp.get("status") == "ok":
            for run in runs_resp.get("runs", []):
                if str(run.get("head_branch") or "") != branch:
                    continue
                run_id = int(run.get("id") or 0)
                status = str(run.get("status") or "")
                conclusion = str(run.get("conclusion") or "")
                last_seen[run_id] = f"{status}/{conclusion}"
                if status == "completed":
                    return run
        time.sleep(poll_s)
    pytest.fail(
        f"Timed out after {timeout_s}s waiting for CI to finish on {repo}@{branch}. "
        f"Observed: {last_seen or 'none'}. The agent must not comment before the pipeline ends."
    )


def _build_oom_event(
    repo: str,
    pr_number: int,
    branch: str,
    run: dict[str, Any],
    oom: dict[str, Any],
) -> AgentEvent:
    conclusion = str(run.get("conclusion") or "")
    kind = "github_pr_build_failure" if conclusion == "failure" else "github_pr"
    event = oom.get("event") or {}
    oom_evidence = [
        {
            "source": "pod",
            "pod": (oom.get("pod") or {}).get("name") if isinstance(oom.get("pod"), dict) else oom.get("pod"),
            "container": oom.get("container"),
            "reason": oom.get("reason") or event.get("reason") or "OOMKilled",
            "message": oom.get("message") or event.get("message"),
            "exit_code": oom.get("exit_code"),
            "event_reason": event.get("reason"),
            "event_message": event.get("message"),
        }
    ]
    return AgentEvent(
        kind=kind,
        title=f"{repo}#{pr_number}: CI {conclusion or 'completed'} for branch {branch}",
        body=(
            "Live scenario_07: wait for pipeline end, then diagnose from cluster OOMKilled "
            "evidence (not from Helm 32Mi/1Mi values alone)."
        ),
        context={
            "repo_full_name": repo,
            "pr_number": pr_number,
            "branch": branch,
            "run_id": run.get("id"),
            "run_url": run.get("html_url", ""),
            "namespace": TARGET_NAMESPACE,
            "oom_evidence": oom_evidence,
            "cluster_snapshot": {"namespace": TARGET_NAMESPACE, "oom_evidence": oom_evidence},
        },
    )



def _is_target_pod(pod_name: str) -> bool:
    return pod_name == TARGET_DEPLOYMENT or pod_name.startswith(f"{TARGET_DEPLOYMENT}-")


def _normalize_oom_text(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "")


def _looks_like_oom(reason: Any, message: Any) -> bool:
    reason_norm = _normalize_oom_text(reason)
    message_norm = _normalize_oom_text(message)
    combined = f"{reason_norm} {message_norm}"
    return any(
        token in combined
        for token in (
            "oomkilled",
            "oomkilling",
            "outofmemory",
            "memory limit",
            "memorylimit",
            "killed process",
        )
    )


def _find_oom_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        if _looks_like_oom(event.get("reason"), event.get("message")):
            return event
    return None


def _pod_has_oomkilled_status(k8s_adapter: K8sAdapter, namespace: str, pod_name: str) -> dict[str, Any] | None:
    pod_resp = k8s_adapter.get_pod(pod_name, namespace)
    if pod_resp.get("status") != "ok":
        return None

    pod_json = pod_resp.get("pod", {})
    status = pod_json.get("status", {})
    status_groups = [
        status.get("containerStatuses", []),
        status.get("initContainerStatuses", []),
    ]

    for group in status_groups:
        for container_status in group:
            for state_key in ("state", "lastState"):
                state = container_status.get(state_key, {})
                for phase_key in ("terminated", "waiting"):
                    phase = state.get(phase_key, {})
                    if _looks_like_oom(phase.get("reason"), phase.get("message")):
                        return {
                            "pod": {
                                "name": pod_name,
                                "phase": status.get("phase", ""),
                                "restarts": sum(
                                    int(item.get("restartCount", 0))
                                    for item in status.get("containerStatuses", [])
                                ),
                            },
                            "pod_status": pod_json,
                            "container": container_status.get("name"),
                            "reason": phase.get("reason"),
                            "message": phase.get("message"),
                            "exit_code": phase.get("exitCode"),
                        }
    return None


def _last_exit_code(k8s_adapter: K8sAdapter, namespace: str, pod_name: str) -> int | None:
    """Terminated exit code of the target container, so evidence carries 137/128 verbatim."""
    pod_resp = k8s_adapter.get_pod(pod_name, namespace)
    if pod_resp.get("status") != "ok":
        return None
    status = pod_resp.get("pod", {}).get("status", {})
    for group_key in ("containerStatuses", "initContainerStatuses"):
        for cs in status.get(group_key, []) or []:
            for state_key in ("lastState", "state"):
                terminated = (cs.get(state_key, {}) or {}).get("terminated", {}) or {}
                if "exitCode" in terminated:
                    return int(terminated["exitCode"])
    return None


def _pod_non_oom_diagnostics(k8s_adapter: K8sAdapter, namespace: str, pod_name: str) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}

    pod_resp = k8s_adapter.get_pod(pod_name, namespace)
    if pod_resp.get("status") == "ok":
        pod_json = pod_resp.get("pod", {})
        status = pod_json.get("status", {})
        diagnostics["phase"] = status.get("phase", "")
        diagnostics["container_reasons"] = []
        for group_key in ("containerStatuses", "initContainerStatuses"):
            for cs in status.get(group_key, []) or []:
                for state_key in ("state", "lastState"):
                    state = cs.get(state_key, {}) or {}
                    for phase_key in ("waiting", "terminated"):
                        phase = state.get(phase_key, {}) or {}
                        reason = str(phase.get("reason") or "")
                        message = str(phase.get("message") or "")
                        if reason or message:
                            diagnostics["container_reasons"].append(
                                {
                                    "container": cs.get("name"),
                                    "state": state_key,
                                    "phase": phase_key,
                                    "reason": reason,
                                    "message": message,
                                }
                            )

    pod_events = k8s_adapter.get_pod_events(pod_name, namespace)
    if pod_events.get("status") == "ok":
        events = pod_events.get("events", []) or []
        diagnostics["recent_events"] = events[-5:]
        diagnostics["event_reasons"] = [str(e.get("reason") or "") for e in events[-10:]]

    return diagnostics


def _build_pr_event(repo: str, pr_number: int, branch: str, body: str) -> AgentEvent:
    """Deterministic unit-test helper (no CI wait). Live tests use `_build_oom_event`."""
    return AgentEvent(
        kind="github_pr",
        title=f"{repo}#{pr_number}: Test PR for scenario 07: OOMKilled due to insufficient memory limits in Helm values",
        body=body,
        context={
            "repo_full_name": repo,
            "pr_number": pr_number,
            "branch": branch,
            "namespace": TARGET_NAMESPACE,
        },
    )


def _wait_for_real_oomkilled(k8s_adapter: K8sAdapter, namespace: str, timeout_s: int, poll_interval_s: int) -> dict[str, Any]:
    """Wait until the live cluster reports a real OOMKilled pod in the target namespace.

    This mirrors the scenario_02 pattern: the test must wait for the environment to fail,
    and only then dispatch the watchdog event.
    """
    deadline = time.time() + timeout_s
    last_seen: dict[str, Any] = {"deployment": TARGET_DEPLOYMENT, "namespace": namespace, "seen_target_pod": False}

    while time.time() < deadline:
        pods_resp = k8s_adapter.get_pods(namespace)
        if pods_resp.get("status") == "ok":
            for pod in pods_resp.get("pods", []):
                pod_name = str(pod.get("name") or "")
                if not _is_target_pod(pod_name):
                    continue
                last_seen["seen_target_pod"] = True
                restarts = int(pod.get("restarts", 0))
                phase = str(pod.get("phase", "")).lower()
                last_seen = {
                    "deployment": TARGET_DEPLOYMENT,
                    "namespace": namespace,
                    "seen_target_pod": True,
                    "pod": pod_name,
                    "phase": phase,
                    "restarts": restarts,
                    "ready": bool(pod.get("ready", False)),
                }
                oom_status = _pod_has_oomkilled_status(k8s_adapter, namespace, pod_name)
                if oom_status:
                    pod_events = k8s_adapter.get_pod_events(pod_name, namespace)
                    if pod_events.get("status") == "ok":
                        matched_event = _find_oom_event(pod_events.get("events", []) or [])
                        if matched_event:
                            oom_status["event"] = matched_event
                            return oom_status
                    return oom_status
                if restarts > 0 or phase not in {"running", "succeeded"}:
                    events_resp = k8s_adapter.get_pod_events(pod_name, namespace)
                    if events_resp.get("status") == "ok":
                        matched_event = _find_oom_event(events_resp.get("events", []) or [])
                        if matched_event:
                            return {
                                "pod": pod,
                                "event": matched_event,
                                "reason": matched_event.get("reason"),
                                "message": matched_event.get("message"),
                                "exit_code": _last_exit_code(k8s_adapter, namespace, pod_name),
                            }
                    last_seen["non_oom_diagnostics"] = _pod_non_oom_diagnostics(k8s_adapter, namespace, pod_name)

        time.sleep(poll_interval_s)

    if not bool(last_seen.get("seen_target_pod")):
        pytest.skip(
            f"Skipped scenario_07: no target deployment '{TARGET_DEPLOYMENT}' pods were observed in namespace '{namespace}' "
            f"within {timeout_s}s. Last seen state: {last_seen}. Verify TEST_SCENARIO07_TARGET_NAMESPACE and "
            "TEST_SCENARIO07_TARGET_DEPLOYMENT."
        )

    pytest.skip(
        f"Skipped scenario_07: the cluster never produced a real OOMKilled event for deployment '{TARGET_DEPLOYMENT}' in "
        f"namespace '{namespace}' within {timeout_s}s. Last seen state: {last_seen}. This environment is not an "
        "OOM scenario (pods stayed healthy or only showed non-OOM events)."
    )


@pytest.fixture(scope="module")
def gh() -> GHAdapter:
    return non_agent_gh_adapter()


@pytest.fixture(scope="module")
def k8s_adapter() -> K8sAdapter:
    return K8sAdapter(kubeconfig=KUBECONFIG)


@pytest.fixture(scope="module")
def mcp_adapter() -> MCPAdapter:
    return MCPAdapter()


@pytest.fixture
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
            "This PR intentionally sets Helm/values-dev.yaml to memory: 1Mi, which "
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
        "This PR sets resources.limits.memory to 1Mi in Helm/values-dev.yaml for a frontend container; "
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
    agent_mode: str,
) -> None:
    pr_number = live_scenario07_pr["pr_number"]
    branch = live_scenario07_pr["branch"]
    from tests.test_scenarios.run_metrics import metrics_snapshot, record_live_comment

    started_at = time.time()
    metrics_before = metrics_snapshot()

    # Wait for the pipeline to finish FIRST. Commenting from Helm 32Mi/1Mi
    # before CI/cluster evidence is the premature-comment failure mode.
    ci_run = _wait_for_ci_terminal(
        gh, TARGET_REPO, branch, CI_WAIT_TIMEOUT_S, CI_POLL_INTERVAL_S
    )
    oom = _wait_for_real_oomkilled(
        k8s_adapter, TARGET_NAMESPACE, OOM_WAIT_TIMEOUT_S, OOM_POLL_INTERVAL_S
    )

    watchdog = AgentWatchdog(adapter=mcp_adapter, graph=build_graph(agent_mode=agent_mode), monitors=[])
    watchdog._dispatch_inner(_build_oom_event(TARGET_REPO, pr_number, branch, ci_run, oom))
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

    comment = bot_comments[-1].get("body", "")
    from tests.test_scenarios.requirement_asserts import (
        assert_any_keyword,
        assert_comment_structure,
        assert_ground_truth_diagnosis,
        assert_keywords_present,
        assert_target_files,
    )

    try:
        assert_comment_structure(comment, require_validation=False)
        assert_keywords_present(comment, ("memory", "oom"))
        assert_any_keyword(comment, ("OOMKilled", "oomkilled", "137"))
        assert_any_keyword(comment, ("1Mi", "32Mi", "limits", "values-dev"))
        spring_boot_guess = "spring boot" in comment.lower() and "256mi" in comment.lower()
        has_oom_evidence = any(token in comment.lower() for token in ("oomkilled", "137", "exit"))
        assert not spring_boot_guess or has_oom_evidence, (
            "Comment guessed a Spring Boot memory recipe without cluster OOM evidence. "
            f"Body:\n{comment[:1200]}"
        )
        assert_target_files(comment, ("Helm/values-dev.yaml",), require_all=False)
        assert_ground_truth_diagnosis(comment, "07")
        record_live_comment(
            scenario_id="07",
            agent_mode=agent_mode,
            comment_body=comment,
            started_at=started_at,
            metrics_before=metrics_before,
            workflow_ok=True,
        )
    except Exception as exc:
        record_live_comment(
            scenario_id="07",
            agent_mode=agent_mode,
            comment_body=comment,
            started_at=started_at,
            metrics_before=metrics_before,
            workflow_ok=False,
            error=str(exc),
        )
        raise
