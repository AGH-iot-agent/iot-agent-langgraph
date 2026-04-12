from __future__ import annotations

from devops_agent.state import AgentState, PlanStep


# ------------------------------------------------------------------ #
#  Keyword helpers                                                     #
# ------------------------------------------------------------------ #

def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in text for kw in keywords)


# ------------------------------------------------------------------ #
#  Plan builders                                                       #
# ------------------------------------------------------------------ #

def _github_actions_plan(state: AgentState, request: str) -> list[PlanStep]:
    context = state.get("context", {})
    run_id = context.get("run_id")
    issue_number = context.get("issue_number") or context.get("pr_number")

    plan: list[PlanStep] = [
        {
            "tool": "github",
            "action": "list_org_workflow_runs",
            "args": {
                "per_page": 10,
                "status": "failure",
            },
        },
        {
            "tool": "github",
            "action": "get_run_summary",
            "args": {
                "run_id": run_id,
            },
        },
    ]

    if issue_number:
        plan.append(
            {
                "tool": "github",
                "action": "comment_pr_or_issue",
                "args": {
                    "issue_number": issue_number,
                    "body": f"Automated CI analysis requested: {request}",
                },
            }
        )

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


# ------------------------------------------------------------------ #
#  Planner node                                                        #
# ------------------------------------------------------------------ #

def planner_node(state: AgentState) -> AgentState:
    request = state.get("request", "")
    revision_count = state.get("revision_count", 0)
    request_lc = request.lower()

    if _contains_any(request_lc, ("github actions", "workflow", "ci/cd", "build", "job", "pipeline", "artifact")):
        plan = _github_actions_plan(state, request)
    elif _contains_any(request_lc, ("pod", "rollout", "deploy", "deployment", "restart", "kubectl", "k8s", "klaster")):
        plan = _kubernetes_plan(state, request)
    elif _contains_any(request_lc, ("log", "logi", "loki", "error", "exception", "stack trace", "traceback")):
        plan = _loki_plan(state, request)
    elif _contains_any(request_lc, ("metryki", "metryk", "metric", "cpu", "memory", "ram", "prometheus", "usage")):
        plan = _prometheus_plan(state, request)
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

    state["plan"] = plan
    state["risk_level"] = "medium"
    return state

    context = state.get("context", {})
    run_id = context.get("run_id")
    issue_number = context.get("issue_number") or context.get("pr_number")

    plan: list[PlanStep] = [
        {
            "tool": "github",
            "action": "get_run_summary",
            "args": {
                "run_id": run_id,
            },
        }
    ]

    if issue_number:
        plan.append(
            {
                "tool": "github",
                "action": "comment_pr_or_issue",
                "args": {
                    "issue_number": issue_number,
                    "body": f"Automated CI analysis requested: {request}",
                },
            }
        )

    return plan


def _base_plan(request: str) -> list[PlanStep]:
    # Plan jest prosty i deterministyczny, aby latwo testowac regresje.
    return [
        {
            "tool": "kubernetes",
            "action": "get_rollout_status",
            "args": {
                "resource": "deployment/iot-agent-gateway-api",
                "namespace": "iotag-dev",
            },
        },
        {
            "tool": "github",
            "action": "comment_pr_or_issue",
            "args": {
                "body": f"Plan generated for request: {request}",
            },
        },
    ]


def planner_node(state: AgentState) -> AgentState:
    request = state.get("request", "")
    revision_count = state.get("revision_count", 0)
    request_lc = request.lower()

    if "github actions" in request_lc or "workflow" in request_lc or "ci" in request_lc:
        plan = _github_actions_plan(state, request)
    else:
        plan = _base_plan(request)

    if revision_count > 0:
        # Przy rewizjach dokladamy krok walidacyjny przed wykonaniem.
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
