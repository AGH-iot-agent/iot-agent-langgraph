from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.security import security_layer
from langchain_core.tools import tool
import logging

logger = logging.getLogger(__name__)

_adapter = MCPAdapter()


def _secure_call(tool_name: str, service: str, action: str, params: dict, dry_run: bool = False) -> dict:
    """
    Execute an adapter call after security checks:
      1. scan tool args for malicious patterns / data-exfiltration URLs
      2. run the adapter call
      3. scan tool output for indirect prompt injection and secrets
    Any high/critical violation is returned as an error dict instead of
    forwarding the unsafe payload to the LLM.
    """
    # -- (1) Scan tool call arguments --
    call_result = security_layer.scan_tool_call(tool_name, params)
    if not call_result.is_safe:
        violations_summary = "; ".join(
            f"{v['threat_type']}({v['severity']})" for v in security_layer.violations_to_dicts(call_result.violations)
        )
        logger.error("[SECURITY] Tool call blocked: tool=%s violations=%s", tool_name, violations_summary)
        return {"status": "error", "message": f"[SECURITY] Tool call blocked: {violations_summary}"}

    # -- (2) Execute --
    raw = _adapter.run(service=service, tool=action, params=params, dry_run=dry_run)

    # -- (3) Scan tool output for indirect injection / leaked secrets --
    raw_str = str(raw)
    output_result = security_layer.scan_tool_output(tool_name, raw_str)
    if not output_result.is_safe:
        violations_summary = "; ".join(
            f"{v['threat_type']}({v['severity']})" for v in security_layer.violations_to_dicts(output_result.violations)
        )
        logger.warning(
            "[SECURITY] Tool output sanitised: tool=%s violations=%s",
            tool_name, violations_summary,
        )
        # Return sanitised output as a warning envelope so the LLM knows
        # the data was altered, but doesn't receive the unsafe content raw.
        return {
            "status": "sanitised",
            "message": f"[SECURITY] Output sanitised — {violations_summary}",
            "data": output_result.sanitized_text,
        }

    return raw


@tool
def k8s_get_pods(namespace: str) -> dict:
    """Get pod status for a Kubernetes namespace."""
    return _secure_call("k8s_get_pods", "kubernetes", "get_pods", {"namespace": namespace})

@tool
def k8s_describe_pod(pod_name: str, namespace: str) -> dict:
    """Describe a specific pod (kubectl describe output) — shows events, resource requests, image."""
    return _secure_call("k8s_describe_pod", "kubernetes", "describe_pod", {"pod_name": pod_name, "namespace": namespace})

@tool
def k8s_get_pod_logs(namespace: str, pod: str, tail: int = 200) -> dict:
    """Fetch recent log lines for a pod directly from Kubernetes (kubectl logs)."""
    return _secure_call("k8s_get_pod_logs", "kubernetes", "get_pod_logs", {"namespace": namespace, "pod": pod, "tail": tail})

@tool
def k8s_get_events(namespace: str) -> dict:
    """List recent Kubernetes events in a namespace — useful for crash/OOM/scheduling failures."""
    return _secure_call("k8s_get_events", "kubernetes", "get_events", {"namespace": namespace})

@tool
def k8s_get_pod_events(pod_name: str, namespace: str) -> dict:
    """List Kubernetes events for a specific pod."""
    return _secure_call("k8s_get_pod_events", "kubernetes", "get_pod_events", {"pod_name": pod_name, "namespace": namespace})

@tool
def k8s_restart_deployment(namespace: str, deployment: str, dry_run: bool = True) -> dict:
    """Restart a Kubernetes deployment. dry_run=True only simulates."""
    return _secure_call(
        "k8s_restart_deployment", "kubernetes", "restart_deployment",
        {"namespace": namespace, "deployment": deployment},
        dry_run=dry_run,
    )

@tool
def k8s_helm_template(name: str, chart: str, values_override: str = "") -> dict:
    """Render a Helm chart with optional values override (YAML string). Used to validate changes before applying."""
    return _secure_call("k8s_helm_template", "kubernetes", "helm_template_render", {"name": name, "chart": chart, "values_override": values_override})

@tool
def k8s_apply_dryrun(manifest_yaml: str, namespace: str = "iotag-sbx") -> dict:
    """Server-side dry-run of a Kubernetes manifest — validates without applying. Safe read-only validation."""
    return _secure_call("k8s_apply_dryrun", "kubernetes", "apply_manifest_dryrun", {"manifest_yaml": manifest_yaml, "namespace": namespace})

@tool
def prom_query(query: str) -> dict:
    """Execute an instant PromQL query against Prometheus. Returns current metric values."""
    return _secure_call("prom_query", "prometheus", "query", {"query": query})

@tool
def prom_query_range(query: str, last_minutes: int = 15) -> dict:
    """Execute a PromQL range query for the last N minutes. Useful for trend analysis."""
    return _secure_call("prom_query_range", "prometheus", "query_range", {"query": query, "last_minutes": last_minutes})

@tool
def loki_get_logs(namespace: str, service: str = "", last_minutes: int = 30, limit: int = 200) -> dict:
    """Fetch recent log lines from Loki for a namespace/service. Uses LogQL label selectors."""
    return _secure_call(
        "loki_get_logs", "loki", "query_range",
        {"namespace": namespace, "service": service, "last_minutes": last_minutes, "limit": limit},
    )

@tool
def gh_list_failed_runs(repo: str, per_page: int = 5) -> dict:
    """List recently failed GitHub Actions workflow runs for a repo."""
    return _secure_call("gh_list_failed_runs", "github", "get_workflow_runs", {"repo": repo, "per_page": per_page})

@tool
def gh_get_job_logs(repo: str, run_id: int, job_id: int) -> dict:
    """Fetch logs for a specific GitHub Actions job. Critical for diagnosing CI failures."""
    return _secure_call("gh_get_job_logs", "github", "get_job_logs", {"repo": repo, "run_id": run_id, "job_id": job_id})

@tool
def gh_get_workflow_runs(repo: str, per_page: int = 10) -> dict:
    """List recent GitHub Actions workflow runs for a repository with their status."""
    return _secure_call("gh_get_workflow_runs", "github", "get_workflow_runs", {"repo": repo, "per_page": per_page})

@tool
def gh_get_file_content(repo: str, path: str, ref: str = "HEAD") -> dict:
    """Fetch content of a file from a GitHub repository at a given ref/branch."""
    return _secure_call("gh_get_file_content", "github", "get_file_content", {"repo": repo, "path": path, "ref": ref})

@tool
def gh_create_pr(repo: str, title: str, body: str, branch: str, base: str = "main") -> dict:
    """Create a GitHub Pull Request. dry_run respected by adapter — default is simulated."""
    return _secure_call("gh_create_pr", "github", "create_pull_request", {"repo": repo, "title": title, "body": body, "branch": branch, "base": base}, dry_run=True)

@tool
def k8s_get_resource_quota(namespace: str) -> dict:
    """Get ResourceQuota usage and hard limits for a Kubernetes namespace. Use to diagnose pod/CPU/memory quota exhaustion."""
    return _secure_call("k8s_get_resource_quota", "kubernetes", "get_resource_quota", {"namespace": namespace})

@tool
def k8s_get_pvc_usage(namespace: str) -> dict:
    """Get PersistentVolumeClaim list with capacity and status for a namespace. Use to diagnose disk pressure issues."""
    return _secure_call("k8s_get_pvc_usage", "kubernetes", "get_pvc_usage", {"namespace": namespace})

@tool
def k8s_get_node_conditions(dummy: str = "") -> dict:
    """Get node conditions (DiskPressure, MemoryPressure, PIDPressure, Ready) for all cluster nodes."""
    return _secure_call("k8s_get_node_conditions", "kubernetes", "get_node_conditions", {})

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
    k8s_get_resource_quota,
    k8s_get_pvc_usage,
    k8s_get_node_conditions,
]
