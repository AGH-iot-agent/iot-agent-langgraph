from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from devops_agent.adapters.kubernetes import KubernetesAdapter
from devops_agent.adapters.prometheus import PrometheusAdapter
from devops_agent.adapters.loki import LokiAdapter
from devops_agent.adapters.github import GitHubAdapter

logger = logging.getLogger(__name__)

mcp = FastMCP("devops-agent")

_k8s   = KubernetesAdapter()
_prom  = PrometheusAdapter()
_loki  = LokiAdapter()
_gh    = GitHubAdapter()

@mcp.tool()
def get_pods(namespace: str = "default") -> dict[str, Any]:
    """List pods and their health status in a namespace."""
    return _k8s.get_pods(namespace)

@mcp.tool()
def get_rollout_status(namespace: str = "default") -> dict[str, Any]:
    """Get deployment rollout status for all deployments in a namespace."""
    return _k8s.get_rollout_status(namespace)

@mcp.tool()
def get_events(namespace: str = "default", event_type: str = "Warning") -> dict[str, Any]:
    """Fetch Kubernetes events, optionally filtered by type (Warning/Normal)."""
    return _k8s.get_events(namespace, event_type=event_type)

@mcp.tool()
def restart_deployment(namespace: str, deployment: str, dry_run: bool = True) -> dict[str, Any]:
    """Rollout-restart a deployment. dry_run=True only simulates the action."""
    return _k8s.restart_deployment(namespace, deployment, dry_run=dry_run)

@mcp.tool()
def scale_deployment(namespace: str, deployment: str, replicas: int, dry_run: bool = True) -> dict[str, Any]:
    """Scale a deployment to the given replica count."""
    return _k8s.scale_deployment(namespace, deployment, replicas, dry_run=dry_run)

@mcp.tool()
def query_prometheus(promql: str, time: str | None = None) -> dict[str, Any]:
    """Run an instant PromQL query. time is an RFC3339 timestamp or None for now."""
    return _prom.query(promql, time=time)

@mcp.tool()
def query_prometheus_range(
    promql: str,
    start: str,
    end: str,
    step: str = "60s",
) -> dict[str, Any]:
    """Run a range PromQL query. start/end are RFC3339 timestamps."""
    return _prom.query_range(promql, start=start, end=end, step=step)

@mcp.tool()
def get_alerts() -> dict[str, Any]:
    """Fetch all currently firing Prometheus alerts."""
    return _prom.get_alerts()

@mcp.tool()
def query_loki(
    logql: str,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Run a LogQL query against Loki. start/end are nanosecond Unix timestamps or RFC3339."""
    return _loki.query(logql, start=start, end=end, limit=limit)

@mcp.tool()
def get_pod_logs(
    namespace: str,
    pod: str,
    tail: int = 200,
    since: str | None = None,
) -> dict[str, Any]:
    """Fetch recent log lines for a specific pod from Loki."""
    return _loki.get_pod_logs(namespace, pod, tail=tail, since=since)

@mcp.tool()
def list_workflow_runs(
    repo: str | None = None,
    status: str = "failure",
    per_page: int = 10,
) -> dict[str, Any]:
    """List recent workflow runs, filtered by status (queued/in_progress/completed/failure)."""
    return _gh.list_workflow_runs(repo=repo, status=status, per_page=per_page)

@mcp.tool()
def get_workflow_run_logs(repo: str, run_id: int) -> dict[str, Any]:
    """Download and return log summary for a specific GitHub Actions run."""
    return _gh.get_run_logs(repo, run_id)

@mcp.tool()
def rerun_workflow(repo: str, run_id: int, dry_run: bool = True) -> dict[str, Any]:
    """Re-trigger a failed workflow run."""
    return _gh.rerun_workflow(repo, run_id, dry_run=dry_run)