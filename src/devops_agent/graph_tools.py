from devops_agent.mcp_adapter import MCPAdapter
from langchain_core.tools import tool

_adapter = MCPAdapter()

@tool
def k8s_get_pods(namespace: str) -> dict:
    """Get pod status for a Kubernetes namespace."""
    return _adapter.run("kubernetes", "get_pods", {"namespace": namespace}, dry_run=False)

@tool
def k8s_describe_pod(pod_name: str, namespace: str) -> dict:
    """Describe a specific pod (kubectl describe output) — shows events, resource requests, image."""
    return _adapter.run("kubernetes", "describe_pod", {"pod_name": pod_name, "namespace": namespace}, dry_run=False)

@tool
def k8s_get_pod_logs(namespace: str, pod: str, tail: int = 200) -> dict:
    """Fetch recent log lines for a pod directly from Kubernetes (kubectl logs)."""
    return _adapter.run("kubernetes", "get_pod_logs", {"namespace": namespace, "pod": pod, "tail": tail}, dry_run=False)

@tool
def k8s_get_events(namespace: str) -> dict:
    """List recent Kubernetes events in a namespace — useful for crash/OOM/scheduling failures."""
    return _adapter.run("kubernetes", "get_events", {"namespace": namespace}, dry_run=False)

@tool
def k8s_get_pod_events(pod_name: str, namespace: str) -> dict:
    """List Kubernetes events for a specific pod."""
    return _adapter.run("kubernetes", "get_pod_events", {"pod_name": pod_name, "namespace": namespace}, dry_run=False)

@tool
def k8s_restart_deployment(namespace: str, deployment: str, dry_run: bool = True) -> dict:
    """Restart a Kubernetes deployment. dry_run=True only simulates."""
    return _adapter.run(
        "kubernetes", "restart_deployment",
        {"namespace": namespace, "deployment": deployment},
        dry_run=dry_run,
    )

@tool
def k8s_helm_template(name: str, chart: str, values_override: str = "") -> dict:
    """Render a Helm chart with optional values override (YAML string). Used to validate changes before applying."""
    return _adapter.run("kubernetes", "helm_template_render", {"name": name, "chart": chart, "values_override": values_override}, dry_run=False)

@tool
def k8s_apply_dryrun(manifest_yaml: str, namespace: str = "iotag-sbx") -> dict:
    """Server-side dry-run of a Kubernetes manifest — validates without applying. Safe read-only validation."""
    return _adapter.run("kubernetes", "apply_manifest_dryrun", {"manifest_yaml": manifest_yaml, "namespace": namespace}, dry_run=False)

@tool
def prom_query(query: str) -> dict:
    """Execute an instant PromQL query against Prometheus. Returns current metric values."""
    return _adapter.run("prometheus", "query", {"query": query}, dry_run=False)

@tool
def prom_query_range(query: str, last_minutes: int = 15) -> dict:
    """Execute a PromQL range query for the last N minutes. Useful for trend analysis."""
    return _adapter.run("prometheus", "query_range", {"query": query, "last_minutes": last_minutes}, dry_run=False)

@tool
def loki_get_logs(namespace: str, service: str = "", last_minutes: int = 30, limit: int = 200) -> dict:
    """Fetch recent log lines from Loki for a namespace/service. Uses LogQL label selectors."""
    return _adapter.run(
        "loki", "query_range",
        {"namespace": namespace, "service": service, "last_minutes": last_minutes, "limit": limit},
        dry_run=False,
    )

@tool
def gh_list_failed_runs(repo: str, per_page: int = 5) -> dict:
    """List recently failed GitHub Actions workflow runs for a repo."""
    return _adapter.run("github", "get_workflow_runs", {"repo": repo, "per_page": per_page}, dry_run=False)

@tool
def gh_get_job_logs(repo: str, run_id: int, job_id: int) -> dict:
    """Fetch logs for a specific GitHub Actions job. Critical for diagnosing CI failures."""
    return _adapter.run("github", "get_job_logs", {"repo": repo, "run_id": run_id, "job_id": job_id}, dry_run=False)

@tool
def gh_get_workflow_runs(repo: str, per_page: int = 10) -> dict:
    """List recent GitHub Actions workflow runs for a repository with their status."""
    return _adapter.run("github", "get_workflow_runs", {"repo": repo, "per_page": per_page}, dry_run=False)

@tool
def gh_get_file_content(repo: str, path: str, ref: str = "HEAD") -> dict:
    """Fetch content of a file from a GitHub repository at a given ref/branch."""
    return _adapter.run("github", "get_file_content", {"repo": repo, "path": path, "ref": ref}, dry_run=False)

@tool
def gh_create_pr(repo: str, title: str, body: str, branch: str, base: str = "main") -> dict:
    """Create a GitHub Pull Request. dry_run respected by adapter — default is simulated."""
    return _adapter.run("github", "create_pull_request", {"repo": repo, "title": title, "body": body, "branch": branch, "base": base}, dry_run=True)

ALL_TOOLS = [
    k8s_get_pods,
    k8s_describe_pod,
    k8s_get_pod_logs,
    k8s_get_events,
    k8s_get_pod_events,
    k8s_restart_deployment,
    k8s_helm_template,
    k8s_apply_dryrun,
    prom_query,
    prom_query_range,
    loki_get_logs,
    gh_list_failed_runs,
    gh_get_job_logs,
    gh_get_workflow_runs,
    gh_get_file_content,
    gh_create_pr,
]
