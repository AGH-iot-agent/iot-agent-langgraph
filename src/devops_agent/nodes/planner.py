from __future__ import annotations

from devops_agent.state import AgentState, PlanStep

def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in text for kw in keywords)

def _github_actions_plan(state: AgentState, request: str) -> list[PlanStep]:
    context = state.get("context", {})
    run_id = context.get("run_id")
    issue_number = context.get("issue_number") or context.get("pr_number")

    plan: list[PlanStep] = [
        {
            "tool": "github",
            "action": "list_workflows",
            "args": {"repo": context.get("repo_full_name", "")},
        },
        {
            "tool": "github",
            "action": "get_workflow_runs",
            "args": {"repo": context.get("repo_full_name", ""), "per_page": 5},
        },
    ]
    return plan


def _kubernetes_plan(state: AgentState, request: str) -> list[PlanStep]:
    context = state.get("context", {})
    namespace = context.get("namespace", "iot-agent")
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
    namespace = context.get("namespace", "iot-agent")
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
    namespace = context.get("namespace", "iot-agent")
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


def _base_plan(request: str) -> list[PlanStep]:
    return [
        {
            "tool": "kubernetes",
            "action": "get_rollout_status",
            "args": {"namespace": "iot-agent"},
        },
        {
            "tool": "github",
            "action": "list_org_workflow_runs",
            "args": {"per_page": 5},
        },
    ]


def planner_node(state: AgentState) -> AgentState:
    request    = state.get("request", "")
    issue_body = state.get("issue_body", "")
    revision_count = state.get("revision_count", 0)
    # Routing na podstawie request + issue_body
    routing_text = (request + " " + issue_body).lower()

    if _contains_any(routing_text, ("github actions", "workflow", "ci/cd", "build", "job", "pipeline", "artifact")):
        plan = _github_actions_plan(state, request)
    elif _contains_any(routing_text, ("pod", "rollout", "deploy", "deployment", "restart", "kubectl", "k8s", "klaster")):
        plan = _kubernetes_plan(state, request)
    elif _contains_any(routing_text, ("log", "logi", "loki", "error", "exception", "stack trace", "traceback")):
        plan = _loki_plan(state, request)
    elif _contains_any(routing_text, ("metryki", "metryk", "metric", "cpu", "memory", "ram", "prometheus", "usage")):
        plan = _prometheus_plan(state, request)
    elif _contains_any(routing_text, ("bug", "błąd", "fix", "napraw", "problem", "issue", "/api/", "endpoint", "update", "create", "delete", "read")):
        plan = _bug_report_plan(state, request)
    else:
        plan = _base_plan(request)

    if revision_count > 0:
        plan.insert(
            0,
            {
                "tool": "kubernetes",
                "action": "get_rollout_status",
                "args": {"namespace": state.get("context", {}).get("namespace", "iot-agent")},
            },
        )


    return {
        **state,
        "plan": plan,
        "risk_level": "medium",
        **({"final_summary": "No plan could be generated for this request."} if not plan else {}),
    }


def _base_plan(request: str) -> list[PlanStep]:
    return [
        {
            "tool": "kubernetes",
            "action": "get_rollout_status",
            "args": {
                "namespace": "iotag-dev",
            },
        },
    ]


def _bug_report_plan(state: AgentState, request: str) -> list[PlanStep]:
    context    = state.get("context", {})
    repo       = context.get("repo_full_name", "")
    issue_body = state.get("issue_body", "")

    terms = _extract_search_terms(issue_body)

    plan: list[PlanStep] = [
        {
            "tool":   "github",
            "action": "search_code",
            "args":   {"repo": repo, "query": term},
        }
        for term in terms[:2]
    ]

    plan.append({
        "tool":   "github",
        "action": "get_commit_history",
        "args":   {"repo": repo, "per_page": 10},
    })

    def _infer_service_from_repo(repo_full_name: str) -> str:
        name = repo_full_name.split("/")[-1]
        name = name.replace("iot-agent-", "")
        return f"{name}-api" if name else ""

    service = context.get("service") or _infer_service_from_repo(repo) or _infer_service(issue_body)
    if service:
        plan.append({
            "tool":   "loki",
            "action": "query_range",
            "args":   {
                "namespace":    context.get("namespace", "iot-agent"),
                "service":      service,
                "last_minutes": 60,
                "limit":        100,
            },
        })

    return plan

def _extract_search_terms(issue_body: str) -> list[str]:
    """Pull endpoint paths and function names from issue text."""
    import re
    terms = []
    paths = re.findall(r'/api/[\w/]+', issue_body)
    terms.extend(p.strip('/').replace('/', ' ') for p in paths[:2])
    funcs = re.findall(r'`(\w+)`', issue_body)
    terms.extend(funcs[:2])
    return terms or ["update user"]


def _infer_service(issue_body: str) -> str:
    """Best-effort: map issue keywords to known service names."""
    body = issue_body.lower()
    mapping = {
        "profile": "profile-api",
        "auth": "authentication-api", 
        "dashboard": "dashboard-api",
        "device": "device-api",
        "gateway": "gateway-api",
    }
    for keyword, service in mapping.items():
        if keyword in body:
            return service
    return ""

def planner_node(state: AgentState) -> AgentState:
    request = state.get("request", "")
    revision_count = state.get("revision_count", 0)
    request_lc = request.lower()

    if "github actions" in request_lc or "workflow" in request_lc or "ci" in request_lc:
        plan = _github_actions_plan(state, request)
    else:
        plan = _base_plan(request)

    if revision_count > 0:
        plan.insert(
            0,
            {
                "tool": "helm",
                "action": "dry_run_upgrade",
                "args": {
                    "release": "iot-agent",
                    "namespace": "iotag-dev",
                },
            },
        )

    state["plan"] = plan
    state["risk_level"] = "medium"
    return state
