from __future__ import annotations

import logging
import time
from typing import Any

from devops_agent.adapters.k8s_adapter import K8sAdapter
from devops_agent.adapters.prometheus_adapter import PrometheusAdapter
from devops_agent.adapters.grafana_adapter import GrafanaAdapter
from devops_agent.adapters.loki_adapter import LokiAdapter
from devops_agent.adapters.github_adapter import GHAdapter
from devops_agent.metrics import agent_metrics
from devops_agent.nodes.critic import forbidden_execute_name, intercepted_forbidden_result

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Any] = {}

def _build_registry() -> dict[str, Any]:
    k8s  = K8sAdapter()
    prom = PrometheusAdapter()
    grafana = GrafanaAdapter()
    loki = LokiAdapter()
    gh   = GHAdapter()

    return {
        "kubernetes": {
            "get_pods":              k8s.get_pods,
            "get_pod":               k8s.get_pod,
            "describe_pod":          k8s.describe_pod,
            "get_events":            k8s.get_events,
            "describe_event":        k8s.describe_event,
            "get_pod_events":        k8s.get_pod_events,
            "get_rollout_status":    k8s.get_rollout_status,
            "get_pod_logs":          k8s.get_pod_logs,
            "restart_deployment":    k8s.restart_deployment,
            "apply_manifest_dryrun": k8s.apply_manifest_dryrun,
            "helm_template_render":  k8s.helm_template_render,
            "helm_validate_deployability": k8s.helm_validate_deployability,
            "get_resource_quota":    k8s.get_resource_quota,
            "get_pvc_usage":         k8s.get_pvc_usage,
            "get_node_conditions":   k8s.get_node_conditions,
            "list_secrets":          k8s.list_secrets,
        },
        "prometheus": {
            "query":                prom.query,
            "query_range":          prom.query_range,
        },
        "grafana": {
            "search_dashboards":    grafana.search_dashboards,
            "get_dashboard":        grafana.get_dashboard,
            "list_folders":         grafana.list_folders,
            "import_dashboard":     grafana.import_dashboard,
        },
        "loki": {
            "query_range":          loki.query_range,
        },
        "github": {
            "get_repo_tree":        gh.get_repo_tree,
            "get_file_content":     gh.get_file_content,
            "search_code":          gh.search_code,
            "get_commit_history":   gh.get_commit_history,
            "list_pull_requests":   gh.list_pull_requests,
            "get_pull_request":     gh.get_pull_request,
            "list_issues":          gh.list_issues,
            "get_issue":            gh.get_issue,
            "create_issue":         gh.create_issue,
            "update_issue":         gh.update_issue,
            "create_issue_comment": gh.create_issue_comment,
            "list_workflows":       gh.list_workflows,
            "get_workflow_runs":    gh.get_workflow_runs,
            "get_job_logs":         gh.get_job_logs,
            "list_org_repos":       gh.list_org_repos,
            "get_pr_comments":      gh.get_pr_comments,
            "get_issue_comments":   gh.get_issue_comments,
            "create_pull_request":  gh.create_pull_request,
            "get_branch_sha":       gh.get_branch_sha,
            "create_branch":        gh.create_branch,
            "commit_file":          gh.commit_file,
            "delete_issue_comment": gh.delete_issue_comment,
            "close_pull_request":   gh.close_pull_request,
            "delete_branch":        gh.delete_branch,
        },
    }

class MCPAdapter:
    def __init__(self) -> None:
        self._registry = _build_registry()

    def run(
        self,
        service: str,
        tool: str,
        params: dict[str, Any],
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """
            Dispatch a tool call to the correct adapter.
            dry_run is injected into params for mutating tools.
        """
        started_at = time.time()

        def _record(status: str, timeout: bool = False) -> None:
            agent_metrics.record_adapter_call(
                {
                    "service": service,
                    "tool": tool,
                    "status": status,
                    "timeout": timeout,
                    "latency_ms": (time.time() - started_at) * 1000.0,
                }
            )

        try:
            lethal = forbidden_execute_name(tool, params)
            if lethal:
                logger.error(
                    "[SAFETY] MCPAdapter intercepted forbidden tool service=%s tool=%s name=%s",
                    service,
                    tool,
                    lethal,
                )
                _record("intercepted")
                return intercepted_forbidden_result(lethal)

            fn = self._registry[service][tool]
        except KeyError:
            logger.error(
                "MCPAdapter.run unknown tool service=%s tool=%s params_keys=%s",
                service,
                tool,
                sorted(list(params.keys())),
            )
            _record("error")
            return {"status": "error", "message": f"Unknown tool: {service}/{tool}"}

        import inspect
        sig = inspect.signature(fn)
        if "dry_run" in sig.parameters:
            params = {**params, "dry_run": dry_run}

        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
        if not has_var_keyword:
            params = {k: v for k, v in params.items() if k in sig.parameters}

        try:
            result = fn(**params)
            _record(str(result.get("status", "ok")), timeout=str(result.get("status", "")).lower() == "degraded")
            return result
        except Exception as exc:
            import httpx, httpcore
            _timeouts = (
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
                httpcore.ReadTimeout,
                httpcore.ConnectTimeout,
            )
            _unreachable = (
                httpx.ConnectError,
                httpx.ReadError,
                httpcore.ConnectError,
                httpcore.ReadError,
                ConnectionRefusedError,
            )
            _expected_config_errors = (
                httpx.UnsupportedProtocol,
                httpx.InvalidURL,
                httpcore.UnsupportedProtocol,
                RuntimeError,
                ValueError,
            )
            if isinstance(exc, _timeouts):
                logger.warning("MCPAdapter.run: %s/%s — timed out (%s)", service, tool, exc)
                _record("degraded", timeout=True)
                return {"status": "degraded", "message": str(exc)}
            if isinstance(exc, _unreachable):
                logger.warning("MCPAdapter.run: %s/%s — service unreachable (%s)", service, tool, exc)
                _record("error")
                return {"status": "error", "message": str(exc)}
            if isinstance(exc, _expected_config_errors):
                logger.warning("MCPAdapter.run: %s/%s — configuration/dependency error (%s)", service, tool, exc)
                _record("error")
                return {"status": "error", "message": str(exc)}

            logger.exception(
                "MCPAdapter.run failed: %s/%s params_keys=%s",
                service,
                tool,
                sorted(list(params.keys())),
            )
            _record("error")
            return {"status": "error", "message": str(exc)}