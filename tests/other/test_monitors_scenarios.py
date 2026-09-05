from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from devops_agent.health import InfraHealthChecker
from devops_agent.monitors.disk_pressure import DiskPressureMonitor
from devops_agent.monitors.github_ci_failure import GitHubCIFailureMonitor
from devops_agent.monitors.github_issues import GitHubIssueMonitor
from devops_agent.monitors.github_pr_build_monitor import GitHubPRBuildMonitor
from devops_agent.monitors.github_prs import GitHubPRMonitor
from devops_agent.monitors.k3s_health import K3sHealthMonitor
from devops_agent.monitors.loki_errors import LokiErrorMonitor
from devops_agent.monitors.prometheus_metrics import PrometheusMetricsMonitor
from devops_agent.monitors.resource_quota import ResourceQuotaMonitor


class StubAdapter:
    def __init__(self, handlers: dict[tuple[str, str], Any]) -> None:
        self._handlers = handlers
        self.calls: list[tuple[str, str, dict[str, Any], bool]] = []

    def run(
        self,
        service: str,
        tool: str,
        params: dict[str, Any],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        self.calls.append((service, tool, params, dry_run))
        handler = self._handlers.get((service, tool))
        if callable(handler):
            fn: Callable[[dict[str, Any]], dict[str, Any]] = handler
            return fn(params)
        if isinstance(handler, dict):
            return handler
        return {"status": "ok"}


def test_k3s_health_monitor_emits_oom_event(monkeypatch) -> None:
    adapter = StubAdapter(
        {
            ("kubernetes", "get_pods"): {
                "status": "ok",
                "pods": [
                    {
                        "name": "api-123",
                        "phase": "Running",
                        "restarts": 5,
                        "ready": False,
                    }
                ],
            },
            ("kubernetes", "get_rollout_status"): {"status": "ok", "deployments": []},
            ("kubernetes", "get_events"): {
                "status": "ok",
                "events": [
                    {
                        "object": "api-123",
                        "reason": "OOMKilled",
                        "message": "Container killed due to OOM",
                    }
                ],
            },
        }
    )
    monitor = K3sHealthMonitor(adapter=adapter, namespace="iotag-dev")
    monkeypatch.setattr(monitor, "_fetch_k8s_items", lambda _kind: [])

    events = monitor.poll()

    assert len(events) == 1
    assert events[0].kind == "k8s_pod_oomkilled"
    assert "api-123" in events[0].title


def test_k3s_health_monitor_emits_oom_from_pod_summary(monkeypatch) -> None:
    adapter = StubAdapter(
        {
            ("kubernetes", "get_pods"): {
                "status": "ok",
                "pods": [
                    {
                        "name": "iot-agent-login-screen-abc",
                        "phase": "Running",
                        "restarts": 4,
                        "ready": False,
                        "oomkilled": True,
                        "last_terminated_reason": "OOMKilled",
                        "last_exit_code": 137,
                        "memory_limits": [{"container": "app", "memory_limit": "1Mi"}],
                    }
                ],
            },
            ("kubernetes", "get_rollout_status"): {"status": "ok", "deployments": []},
            ("kubernetes", "get_events"): {"status": "ok", "events": []},
        }
    )
    monitor = K3sHealthMonitor(adapter=adapter, namespace="iotag-sbx")
    monkeypatch.setattr(monitor, "_fetch_k8s_items", lambda _kind: [])

    events = monitor.poll()

    assert len(events) == 1
    assert events[0].kind == "k8s_pod_oomkilled"


def test_loki_error_monitor_emits_service_spike_event() -> None:
    stream_values = [["1", "error one"] for _ in range(20)]
    adapter = StubAdapter(
        {
            ("loki", "query_range"): {
                "status": "ok",
                "lines": [],
                "streams": [
                    {
                        "labels": {"app": "iot-agent-api"},
                        "values": stream_values,
                    }
                ],
            }
        }
    )
    monitor = LokiErrorMonitor(adapter=adapter, namespace="iotag-dev")

    events = monitor.poll()

    assert len(events) == 1
    assert events[0].kind == "loki_error_spike"
    assert "iot-agent-api" in events[0].title


def test_prometheus_metrics_monitor_emits_request_spike_event() -> None:
    def prom_query(params: dict[str, Any]) -> dict[str, Any]:
        query = params.get("query", "")
        if "container_cpu_usage_seconds_total" in query:
            return {
                "status": "ok",
                "results": [{"metric": {"pod": "api-1"}, "value": ["1", "0.20"]}],
            }
        if "http_server_requests_seconds_bucket" in query:
            return {
                "status": "ok",
                "results": [
                    {
                        "metric": {"pod": "api-1", "uri": "/health"},
                        "value": ["1", "0.10"],
                    }
                ],
            }
        if 'status=~"5.."' in query:
            return {
                "status": "ok",
                "results": [
                    {
                        "metric": {"pod": "api-1", "uri": "/health"},
                        "value": ["1", "0.001"],
                    }
                ],
            }
        if "http_server_requests_seconds_count" in query and "[1m]" in query:
            return {
                "status": "ok",
                "results": [{"metric": {"pod": "api-1"}, "value": ["1", "150.0"]}],
            }
        return {"status": "ok", "results": []}

    adapter = StubAdapter({("prometheus", "query"): prom_query})
    monitor = PrometheusMetricsMonitor(adapter=adapter, namespace="iotag-dev")

    events = monitor.poll()

    assert len(events) == 1
    assert events[0].kind == "request_rate_spike"
    assert "api-1" in events[0].title


def test_resource_quota_monitor_emits_quota_event() -> None:
    adapter = StubAdapter(
        {
            ("kubernetes", "get_resource_quota"): {
                "status": "ok",
                "quotas": [
                    {
                        "name": "default",
                        "resources": [
                            {
                                "resource": "requests.cpu",
                                "used": "9",
                                "hard": "10",
                            }
                        ],
                    }
                ],
            }
        }
    )
    monitor = ResourceQuotaMonitor(adapter=adapter, namespace="iotag-dev")

    events = monitor.poll()

    assert len(events) == 1
    assert events[0].kind == "namespace_quota_exceeded"


def test_disk_pressure_monitor_emits_pvc_and_node_events() -> None:
    def prom_query(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "results": [
                {
                    "metric": {"persistentvolumeclaim": "data-pvc"},
                    "value": ["1", "0.92"],
                }
            ],
        }

    adapter = StubAdapter(
        {
            ("prometheus", "query"): prom_query,
            ("kubernetes", "get_node_conditions"): {
                "status": "ok",
                "nodes": [
                    {
                        "name": "node-a",
                        "conditions": {"DiskPressure": "True"},
                    }
                ],
            },
        }
    )
    monitor = DiskPressureMonitor(adapter=adapter, namespace="iotag-dev")

    events = monitor.poll()

    kinds = {event.kind for event in events}
    assert "pvc_disk_pressure" in kinds
    assert "node_disk_pressure" in kinds


def test_github_issue_monitor_emits_labeled_issue_event() -> None:
    adapter = StubAdapter(
        {
            ("github", "list_org_repos"): {
                "status": "ok",
                "repos_with_issues": [{"full_name": "AGH-iot-agent/iot-agent-langgraph"}],
            },
            ("github", "list_issues"): {
                "status": "ok",
                "issues": [
                    {
                        "number": 17,
                        "title": "K8s pod crash loop in dev",
                        "body": "Pods crashlooping after a bad probe.",
                        "labels": ["iot-devops-agent", "bug"],
                    }
                ],
            },
            ("github", "get_issue_comments"): {"status": "ok", "comments": []},
        }
    )
    monitor = GitHubIssueMonitor(adapter=adapter, org="AGH-iot-agent")

    events = monitor.poll()

    assert len(events) == 1
    assert events[0].kind == "github_issue"
    assert "#17" in events[0].title
    assert events[0].body == "Pods crashlooping after a bad probe."


def test_github_pr_monitor_emits_labeled_pr_event() -> None:
    def workflow_runs(params: dict[str, Any]) -> dict[str, Any]:
        if params.get("status") in {"in_progress", "queued"}:
            return {"status": "ok", "runs": []}
        return {
            "status": "ok",
            "runs": [
                {
                    "id": 9,
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": "abc123",
                    "head_branch": "feature/fix-rollout",
                }
            ],
        }

    adapter = StubAdapter(
        {
            ("github", "list_org_repos"): {
                "status": "ok",
                "repos_with_prs": [{"full_name": "AGH-iot-agent/iot-agent-langgraph"}],
            },
            ("github", "list_pull_requests"): {
                "status": "ok",
                "pull_requests": [
                    {
                        "number": 21,
                        "title": "Fix helm rollout issue",
                        "labels": ["iot-devops-agent", "deployment"],
                        "head": {"ref": "feature/fix-rollout", "sha": "abc123"},
                    }
                ],
            },
            ("github", "get_pr_comments"): {"status": "ok", "comments": []},
            ("github", "get_workflow_runs"): workflow_runs,
        }
    )
    monitor = GitHubPRMonitor(adapter=adapter, org="AGH-iot-agent")

    events = monitor.poll()

    assert len(events) == 1
    assert events[0].kind == "github_pr"
    assert "#21" in events[0].title


def _labeled_pr(sha: str = "deadbeef") -> dict[str, Any]:
    return {
        "number": 21,
        "title": "Fix helm rollout issue",
        "labels": ["iot-devops-agent", "deployment"],
        "head": {"ref": "feature/fix-rollout", "sha": sha},
    }


def test_github_pr_monitor_waits_while_ci_in_progress() -> None:
    adapter = StubAdapter(
        {
            ("github", "list_org_repos"): {
                "status": "ok",
                "repos_with_prs": [{"full_name": "AGH-iot-agent/iot-agent-login-screen"}],
            },
            ("github", "list_pull_requests"): {
                "status": "ok",
                "pull_requests": [_labeled_pr()],
            },
            ("github", "get_pr_comments"): {"status": "ok", "comments": []},
            ("github", "get_workflow_runs"): {
                "status": "ok",
                "runs": [
                    {
                        "id": 1,
                        "status": "in_progress",
                        "conclusion": None,
                        "head_sha": "deadbeef",
                    }
                ],
            },
        }
    )
    monitor = GitHubPRMonitor(adapter=adapter, org="AGH-iot-agent")
    assert monitor.poll() == []
    # Must not mark the PR as commented while CI is still running.
    assert monitor.poll() == []


def test_github_pr_monitor_skips_failed_ci_for_pr_build_monitor() -> None:
    def workflow_runs(params: dict[str, Any]) -> dict[str, Any]:
        if params.get("status") in {"in_progress", "queued"}:
            return {"status": "ok", "runs": []}
        return {
            "status": "ok",
            "runs": [
                {
                    "id": 2,
                    "status": "completed",
                    "conclusion": "failure",
                    "head_sha": "deadbeef",
                }
            ],
        }

    adapter = StubAdapter(
        {
            ("github", "list_org_repos"): {
                "status": "ok",
                "repos_with_prs": [{"full_name": "AGH-iot-agent/iot-agent-login-screen"}],
            },
            ("github", "list_pull_requests"): {
                "status": "ok",
                "pull_requests": [_labeled_pr()],
            },
            ("github", "get_pr_comments"): {"status": "ok", "comments": []},
            ("github", "get_workflow_runs"): workflow_runs,
        }
    )
    monitor = GitHubPRMonitor(adapter=adapter, org="AGH-iot-agent")
    assert monitor.poll() == []


def test_github_ci_failure_monitor_emits_event_for_failed_branch_run() -> None:
    now = datetime.now(timezone.utc)
    created_at = (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def workflow_runs(params: dict[str, Any]) -> dict[str, Any]:
        if params.get("status") == "in_progress":
            return {"status": "ok", "runs": []}
        return {
            "status": "ok",
            "runs": [
                {
                    "id": 301,
                    "status": "completed",
                    "conclusion": "failure",
                    "head_branch": "feature/fix-auth",
                    "head_sha": "abcdef0123",
                    "name": "build",
                    "html_url": "https://example/run/301",
                    "pull_requests": [],
                    "created_at": created_at,
                }
            ],
        }

    def pull_requests(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "pull_requests": [
                {
                    "number": 44,
                    "head": {"ref": "feature/fix-auth"},
                }
            ],
        }

    adapter = StubAdapter(
        {
            ("github", "list_org_repos"): {
                "status": "ok",
                "repos": [{"full_name": "AGH-iot-agent/iot-agent-langgraph"}],
            },
            ("github", "get_workflow_runs"): workflow_runs,
            ("github", "list_pull_requests"): pull_requests,
            ("github", "get_pr_comments"): {"status": "ok", "comments": []},
        }
    )
    monitor = GitHubCIFailureMonitor(adapter=adapter, org="AGH-iot-agent")
    monitor._start_time = 0.0

    events = monitor.poll()

    assert len(events) == 1
    assert events[0].kind == "github_ci_failure"


def test_github_pr_build_monitor_emits_event_for_failed_pr_run() -> None:
    def workflow_runs(params: dict[str, Any]) -> dict[str, Any]:
        if params.get("status") == "in_progress":
            return {"status": "ok", "runs": []}
        return {
            "status": "ok",
            "runs": [
                {
                    "id": 501,
                    "status": "completed",
                    "conclusion": "failure",
                    "head_branch": "feature/retry-ci",
                    "head_sha": "sha501",
                    "name": "ci",
                    "html_url": "https://example/run/501",
                    "pull_requests": [
                        {
                            "number": 77,
                            "state": "open",
                            "base": {"ref": "main"},
                        }
                    ],
                }
            ],
        }

    adapter = StubAdapter(
        {
            ("github", "list_org_repos"): {
                "status": "ok",
                "repos": [{"full_name": "AGH-iot-agent/iot-agent-langgraph"}],
            },
            ("github", "get_workflow_runs"): workflow_runs,
            ("github", "get_pr_comments"): {"status": "ok", "comments": []},
        }
    )
    monitor = GitHubPRBuildMonitor(adapter=adapter, org="AGH-iot-agent")

    events = monitor.poll()

    assert len(events) == 1
    assert events[0].kind == "github_pr_build_failure"
    assert events[0].context.get("namespace") == "iotag-sbx"


def test_github_pr_build_monitor_attaches_oom_evidence() -> None:
    def workflow_runs(params: dict[str, Any]) -> dict[str, Any]:
        if params.get("status") == "in_progress":
            return {"status": "ok", "runs": []}
        return {
            "status": "ok",
            "runs": [
                {
                    "id": 777,
                    "status": "completed",
                    "conclusion": "failure",
                    "head_branch": "test/scenario_07_oom",
                    "head_sha": "sha777",
                    "name": "ci",
                    "html_url": "https://example/run/777",
                    "pull_requests": [
                        {"number": 213, "state": "open", "base": {"ref": "main"}},
                    ],
                }
            ],
        }

    adapter = StubAdapter(
        {
            ("github", "list_org_repos"): {
                "status": "ok",
                "repos": [{"full_name": "AGH-iot-agent/iot-agent-login-screen"}],
            },
            ("github", "get_workflow_runs"): workflow_runs,
            ("github", "get_pr_comments"): {"status": "ok", "comments": []},
            ("kubernetes", "get_pods"): {
                "status": "ok",
                "pods": [
                    {
                        "name": "iot-agent-login-screen-xyz",
                        "oomkilled": True,
                        "last_terminated_reason": "OOMKilled",
                        "last_exit_code": 137,
                        "memory_limits": [{"container": "app", "memory_limit": "1Mi"}],
                    }
                ],
            },
            ("kubernetes", "get_events"): {
                "status": "ok",
                "events": [
                    {
                        "object": "iot-agent-login-screen-xyz",
                        "reason": "OOMKilled",
                        "message": "Container app was OOMKilled",
                    }
                ],
            },
        }
    )
    monitor = GitHubPRBuildMonitor(adapter=adapter, org="AGH-iot-agent")

    events = monitor.poll()

    assert len(events) == 1
    evidence = events[0].context.get("oom_evidence") or []
    assert evidence
    assert any(item.get("reason") == "OOMKilled" for item in evidence)


def test_infra_health_checker_reports_adapters_reachable() -> None:
    adapter = StubAdapter(
        {
            ("kubernetes", "get_pods"): {"status": "ok", "pods": []},
            ("prometheus", "query"): {"status": "degraded", "message": "timeout"},
            ("loki", "query_range"): {"status": "ok", "lines": []},
            ("github", "list_org_repos"): {"status": "error", "message": "missing org"},
        }
    )
    checker = InfraHealthChecker(adapter=adapter, namespace="iotag-dev")

    report = checker.check_all().to_dict()

    assert report["status"] == "degraded"
    assert "github" in report["unhealthy"]

    called_tools = {(service, tool) for service, tool, _, _ in adapter.calls}
    assert ("kubernetes", "get_pods") in called_tools
    assert ("prometheus", "query") in called_tools
    assert ("loki", "query_range") in called_tools
    assert ("github", "list_org_repos") in called_tools
