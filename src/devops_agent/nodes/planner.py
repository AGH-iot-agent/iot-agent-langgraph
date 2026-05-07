from __future__ import annotations

from devops_agent.state import AgentState, PlanStep


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in text for kw in keywords)


def _github_actions_plan(state: AgentState, request: str) -> list[PlanStep]:
    context = state.get("context", {})
    repo = context.get("repo_full_name", "")
    run_id = context.get("run_id")

    plan: list[PlanStep] = [
        {
            "tool": "github",
            "action": "list_workflows",
            "args": {"repo": repo},
        },
        {
            "tool": "github",
            "action": "get_workflow_runs",
            "args": {"repo": repo, "per_page": 5},
        },
    ]

    if run_id:
        plan.append({
            "tool": "github",
            "action": "get_job_logs",
            "args": {"repo": repo, "run_id": run_id, "job_id": 0},
        })
    return plan


def _ci_failure_plan(state: AgentState, request: str) -> list[PlanStep]:
    """Plan for analyzing a CI failure and preparing a fix."""
    context = state.get("context", {})
    repo = context.get("repo_full_name", "")
    run_id = context.get("run_id", 0)
    branch = context.get("branch", "main")

    return [
        {
            "tool": "github",
            "action": "get_job_logs",
            "args": {"repo": repo, "run_id": run_id, "job_id": 0},
        },
        {
            "tool": "github",
            "action": "get_file_content",
            "args": {"repo": repo, "path": ".github/workflows/ci.yml", "ref": branch},
        },
        {
            "tool": "github",
            "action": "get_file_content",
            "args": {"repo": repo, "path": "Helm/values-dev.yaml", "ref": branch},
        },
        {
            "tool": "github",
            "action": "get_file_content",
            "args": {"repo": repo, "path": "Helm/values-sbx.yaml", "ref": branch},
        },
    ]


def _kubernetes_plan(state: AgentState, request: str) -> list[PlanStep]:
    context = state.get("context", {})
    namespace = context.get("namespace", "iotag-dev")
    return [
        {
            "tool": "kubernetes",
            "action": "get_rollout_status",
            "args": {"namespace": namespace},
        },
        {
            "tool": "kubernetes",
            "action": "get_pods",
            "args": {"namespace": namespace},
        },
        {
            "tool": "kubernetes",
            "action": "get_events",
            "args": {"namespace": namespace},
        },
    ]


def _loki_plan(state: AgentState, request: str) -> list[PlanStep]:
    context = state.get("context", {})
    namespace = context.get("namespace", "iotag-dev")
    service = context.get("service", "")
    return [
        {
            "tool": "loki",
            "action": "query_range",
            "args": {
                "namespace": namespace,
                "service": service,
                "last_minutes": 30,
                "limit": 200,
            },
        }
    ]


def _prometheus_plan(state: AgentState, request: str) -> list[PlanStep]:
    context = state.get("context", {})
    namespace = context.get("namespace", "iotag-dev")
    return [
        {
            "tool": "prometheus",
            "action": "query",
            "args": {
                "query": f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[5m])) by (pod)',
            },
        },
        {
            "tool": "prometheus",
            "action": "query",
            "args": {
                "query": f'sum(container_memory_working_set_bytes{{namespace="{namespace}"}}) by (pod)',
            },
        },
    ]


def _bug_report_plan(state: AgentState, request: str) -> list[PlanStep]:
    context = state.get("context", {})
    repo = context.get("repo_full_name", "")
    issue_body = state.get("issue_body", "")
    terms = _extract_search_terms(issue_body)

    plan: list[PlanStep] = [
        {
            "tool": "github",
            "action": "search_code",
            "args": {"repo": repo, "query": term},
        }
        for term in terms[:2]
    ]

    plan.append({
        "tool": "github",
        "action": "get_commit_history",
        "args": {"repo": repo, "per_page": 10},
    })

    service = context.get("service") or _infer_service_from_repo(repo) or _infer_service(issue_body)
    if service:
        plan.append({
            "tool": "loki",
            "action": "query_range",
            "args": {
                "namespace": context.get("namespace", "iotag-dev"),
                "service": service,
                "last_minutes": 60,
                "limit": 100,
            },
        })

    return plan


def _prometheus_alert_plan(state: AgentState, request: str) -> list[PlanStep]:
    """Plan for Prometheus alerts (high CPU/latency/error rate) — include Loki log correlation."""
    context = state.get("context", {})
    namespace = context.get("namespace", "iotag-dev")
    pod = context.get("pod", "")
    service = context.get("service", "") or (pod.rsplit("-", 2)[0] if pod else "")

    return [
        {
            "tool": "prometheus",
            "action": "query",
            "args": {
                "query": f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[5m])) by (pod)',
            },
        },
        {
            "tool": "prometheus",
            "action": "query",
            "args": {
                "query": (
                    f'histogram_quantile(0.99, sum(rate('
                    f'http_server_requests_seconds_bucket{{namespace="{namespace}"}}[5m]'
                    f')) by (le, pod))'
                ),
            },
        },
        {
            "tool": "prometheus",
            "action": "query",
            "args": {
                "query": f'sum(container_memory_working_set_bytes{{namespace="{namespace}"}}) by (pod)',
            },
        },
        {
            "tool": "loki",
            "action": "query_range",
            "args": {
                "namespace": namespace,
                "service": service,
                "last_minutes": 15,
                "limit": 200,
            },
        },
        {
            "tool": "kubernetes",
            "action": "get_pods",
            "args": {"namespace": namespace},
        },
        {
            "tool": "kubernetes",
            "action": "get_events",
            "args": {"namespace": namespace},
        },
    ]


def _orphaned_resource_plan(state: AgentState, request: str) -> list[PlanStep]:
    """Plan for detecting and cleaning orphaned K8s resources."""
    context = state.get("context", {})
    namespace = context.get("namespace", "iotag-dev")
    return [
        {
            "tool": "kubernetes",
            "action": "get_pods",
            "args": {"namespace": namespace},
        },
        {
            "tool": "kubernetes",
            "action": "get_events",
            "args": {"namespace": namespace},
        },
        {
            "tool": "kubernetes",
            "action": "get_rollout_status",
            "args": {"namespace": namespace},
        },
    ]


def _base_plan(request: str) -> list[PlanStep]:
    return [
        {
            "tool": "kubernetes",
            "action": "get_rollout_status",
            "args": {"namespace": "iotag-dev"},
        },
        {
            "tool": "github",
            "action": "get_workflow_runs",
            "args": {"per_page": 5},
        },
    ]


def _infra_issue_plan(state: AgentState, request: str) -> list[PlanStep]:
    """Plan for github_issue / github_pr events — fetch real Helm + CI files for fix proposals."""
    context = state.get("context", {})
    repo = context.get("repo_full_name", "")
    branch = (
        context.get("branch")
        or (context.get("pr") or {}).get("head", {}).get("ref", "")
        or "main"
    )
    return [
        {
            "tool": "github",
            "action": "get_file_content",
            "args": {"repo": repo, "path": "Helm/values-dev.yaml", "ref": branch},
        },
        {
            "tool": "github",
            "action": "get_file_content",
            "args": {"repo": repo, "path": "Helm/values-sbx.yaml", "ref": branch},
        },
        {
            "tool": "github",
            "action": "get_file_content",
            "args": {"repo": repo, "path": ".github/workflows/ci.yml", "ref": branch},
        },
        {
            "tool": "github",
            "action": "get_commit_history",
            "args": {"repo": repo, "per_page": 5},
        },
    ]


def _extract_search_terms(issue_body: str) -> list[str]:
    import re
    terms = []
    for m in re.finditer(r"/[\w/-]+", issue_body):
        terms.append(m.group())
    for m in re.finditer(r"\b([a-z]+(?:Service|Controller|Repository|Handler|Manager))\b", issue_body):
        terms.append(m.group())
    return list(dict.fromkeys(terms))

def _infer_service_from_repo(repo_full_name: str) -> str:
    name = repo_full_name.split("/")[-1]
    name = name.replace("iot-agent-", "")
    return f"{name}-api" if name else ""

def _infer_service(text: str) -> str:
    import re
    match = re.search(r"iot-agent-[\w-]+", text)
    return match.group() if match else ""


def planner_node(state: AgentState) -> AgentState:
    request = state.get("request", "")
    issue_body = state.get("issue_body", "")
    event_kind = state.get("event_kind", "")
    revision_count = state.get("revision_count", 0)

    routing_text = (request + " " + issue_body).lower()

    if event_kind in ("github_ci_failure", "github_pr_build_failure"):
        plan = _ci_failure_plan(state, request)
    elif event_kind in ("github_issue", "github_pr"):
        plan = _infra_issue_plan(state, request)
    elif event_kind in ("high_cpu_usage", "high_http_latency", "high_error_rate"):
        plan = _prometheus_alert_plan(state, request)
    elif event_kind in ("k8s_orphaned_resource",):
        plan = _orphaned_resource_plan(state, request)
    elif event_kind in ("istio_vs_misconfiguration",):
        plan = _kubernetes_plan(state, request)
    elif _contains_any(routing_text, ("github actions", "workflow", "ci/cd", "build", "job", "pipeline", "artifact", "ci failure")):
        plan = _github_actions_plan(state, request)
    elif _contains_any(routing_text, ("pod", "rollout", "deploy", "deployment", "restart", "kubectl", "k8s", "klaster")):
        plan = _kubernetes_plan(state, request)
    elif _contains_any(routing_text, ("log", "logi", "loki", "error", "exception", "stack trace", "traceback")):
        plan = _loki_plan(state, request)
    elif _contains_any(routing_text, ("metric", "cpu", "memory", "ram", "prometheus", "usage")):
        plan = _prometheus_plan(state, request)
    elif _contains_any(routing_text, ("bug", "fix", "problem", "issue", "/api/", "endpoint")):
        plan = _bug_report_plan(state, request)
    else:
        plan = _base_plan(request)

    if revision_count > 0:
        plan.insert(0, {
            "tool": "kubernetes",
            "action": "get_rollout_status",
            "args": {"namespace": state.get("context", {}).get("namespace", "iotag-dev")},
        })

    return {
        **state,
        "plan": plan,
        "risk_level": "medium",
        **({"final_summary": "No plan could be generated for this request."} if not plan else {}),
    }
