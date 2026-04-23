from devops_agent.mcp_adapter import MCPAdapter
from langchain_core.tools import tool

_adapter = MCPAdapter()

@tool
def k8s_get_pods(namespace: str) -> dict:
    """Get pod status for a Kubernetes namespace."""
    return _adapter.run("kubernetes", "get_pods", {"namespace": namespace}, dry_run=False)


@tool
def k8s_restart_deployment(namespace: str, deployment: str, dry_run: bool = True) -> dict:
    """Restart a Kubernetes deployment. dry_run=True only simulates."""
    return _adapter.run(
        "kubernetes", "restart_deployment",
        {"namespace": namespace, "deployment": deployment},
        dry_run=dry_run,
    )

@tool
def prom_get_alerts() -> dict:
    """Fetch all currently firing Prometheus alerts."""
    return _adapter.run("prometheus", "get_alerts", {}, dry_run=False)

@tool
def loki_get_pod_logs(namespace: str, pod: str, tail: int = 100) -> dict:
    """Fetch recent log lines for a pod from Loki."""
    return _adapter.run(
        "loki", "get_pod_logs",
        {"namespace": namespace, "pod": pod, "tail": tail},
        dry_run=False,
    )

@tool
def gh_list_failed_runs(per_page: int = 5) -> dict:
    """List recently failed GitHub Actions workflow runs."""
    return _adapter.run(
        "github", "list_workflow_runs",
        {"status": "failure", "per_page": per_page},
        dry_run=False,
    )

ALL_TOOLS = [
    k8s_get_pods,
    k8s_restart_deployment,
    prom_get_alerts,
    loki_get_pod_logs,
    gh_list_failed_runs,
]
