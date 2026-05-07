from __future__ import annotations

import logging
from typing import Any

from devops_agent.adapters.k8s_adapter import K8sAdapter
from devops_agent.adapters.prometheus_adapter import PrometheusAdapter
from devops_agent.adapters.loki_adapter import LokiAdapter
from devops_agent.adapters.github_adapter import GHAdapter

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Any] = {}

def _build_registry() -> dict[str, Any]:
    k8s  = K8sAdapter()
    prom = PrometheusAdapter()
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
        },
        "prometheus": {
            "query":                prom.query,
            "query_range":          prom.query_range,
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
        try:
            fn = self._registry[service][tool]
        except KeyError:
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
            return result
        except Exception as exc:
            import httpx, httpcore
            if isinstance(exc, (httpx.ConnectError, httpcore.ConnectError, ConnectionRefusedError)):
                logger.warning("MCPAdapter.run: %s/%s — service unreachable (%s)", service, tool, exc)
            else:
                logger.exception("MCPAdapter.run failed: %s/%s", service, tool)
            return {"status": "error", "message": str(exc)}