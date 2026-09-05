from __future__ import annotations

from devops_agent.adapters.k8s_adapter import summarize_pod
from devops_agent.k8s_signals import collect_oom_evidence, pod_is_oomkilled
from devops_agent.nodes.ci_fixer import _cluster_diagnostic_block
from devops_agent.nodes.finalizer import finalizer_node
from devops_agent.nodes.planner import _ci_failure_plan, _infer_service_from_repo


def test_summarize_pod_exposes_oomkilled_and_memory_limit() -> None:
    pod = {
        "metadata": {"name": "iot-agent-login-screen-abc"},
        "spec": {
            "nodeName": "k3s",
            "containers": [
                {"name": "app", "resources": {"limits": {"memory": "1Mi"}}},
            ],
        },
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "app",
                    "ready": False,
                    "restartCount": 6,
                    "lastState": {
                        "terminated": {"reason": "OOMKilled", "exitCode": 137},
                    },
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                }
            ],
        },
    }
    summary = summarize_pod(pod)
    assert summary["oomkilled"] is True
    assert summary["last_terminated_reason"] == "OOMKilled"
    assert summary["last_exit_code"] == 137
    assert summary["memory_limits"] == [{"container": "app", "memory_limit": "1Mi"}]
    assert pod_is_oomkilled(summary)


def test_collect_oom_evidence_from_pod_and_event() -> None:
    pods = [{"name": "iot-agent-login-screen-abc", "oomkilled": True, "last_exit_code": 137}]
    events = [
        {
            "object": "worker-node",
            "object_kind": "Node",
            "reason": "OOMKilling",
            "message": "Memory cgroup out of memory: Kill process in iot-agent-login-screen-abc",
        }
    ]
    evidence = collect_oom_evidence(pods, events, service="iot-agent-login-screen")
    assert len(evidence) >= 2
    sources = {item["source"] for item in evidence}
    assert "pod" in sources
    assert "event" in sources


def test_cluster_diagnostic_block_survives_without_logs() -> None:
    text = _cluster_diagnostic_block(
        execution_results=[],
        context={
            "oom_evidence": [{"source": "pod", "pod": "login-abc", "reason": "OOMKilled"}],
        },
    )
    assert "OOMKilled" in text
    assert "CLUSTER OOM EVIDENCE" in text


def test_finalizer_keeps_pr_fix_comment_for_ci_events() -> None:
    state = {
        "event_kind": "github_pr_build_failure",
        "final_summary": "### Root Cause\nOOMKilled from 1Mi limit",
        "execution_summary": "## Root Cause\nI guessed Spring Boot 512Mi",
    }
    result = finalizer_node(state)
    assert "OOMKilled from 1Mi" in result["final_summary"]
    assert "Spring Boot" not in result["final_summary"]


def test_ci_failure_plan_queries_cluster_before_github_logs() -> None:
    plan = _ci_failure_plan(
        {
            "context": {
                "repo_full_name": "AGH-iot-agent/iot-agent-login-screen",
                "run_id": 1,
                "branch": "test/scenario_07",
                "namespace": "iotag-sbx",
                "oom_evidence": [{"pod": "iot-agent-login-screen-abc"}],
            }
        },
        "build failed",
    )
    actions = [(step["tool"], step["action"]) for step in plan]
    assert actions[0] == ("kubernetes", "get_pods")
    assert ("github", "get_job_logs") in actions
    assert actions.index(("kubernetes", "get_pods")) < actions.index(("github", "get_job_logs"))
    assert ("kubernetes", "describe_pod") in actions
    assert _infer_service_from_repo("AGH-iot-agent/iot-agent-login-screen") == "iot-agent-login-screen"


def test_infra_pr_plan_fetches_ci_and_cluster_before_helm_values() -> None:
    from devops_agent.nodes.planner import _infra_issue_plan

    plan = _infra_issue_plan(
        {
            "event_kind": "github_pr",
            "context": {
                "repo_full_name": "AGH-iot-agent/iot-agent-login-screen",
                "branch": "test/scenario_07",
                "namespace": "iotag-sbx",
                "run_id": 44,
            },
        },
        "memory 32Mi",
    )
    actions = [(step["tool"], step["action"]) for step in plan]
    assert actions[0] == ("github", "get_workflow_runs")
    assert ("github", "get_job_logs") in actions
    assert actions.index(("kubernetes", "get_pods")) < actions.index(
        ("github", "get_file_content")
    )
