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
    # Deploy failures land in iotag-sbx; PR build failures in iotag-dev
    namespace = context.get("namespace", "iotag-sbx")

    plan: list[PlanStep] = [
        {
            "tool": "github",
            "action": "get_job_logs",
            "args": {"repo": repo, "run_id": run_id, "job_id": 0},
        },
        # Fetch the CI workflow file (always named ci.yml in this project — not build.yml)
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
        # K8s state in the deploy target namespace — critical for deploy-step failures
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

    # Fetch recent pod logs from Loki to catch CrashLoopBackOff / OOMKilled messages
    service = _infer_service_from_repo(repo)
    if service:
        plan.append({
            "tool": "loki",
            "action": "query_range",
            "args": {
                "namespace": namespace,
                "service": service,
                "last_minutes": 15,
                "limit": 100,
            },
        })

    return plan


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


def _scalability_plan(state: AgentState, request: str) -> list[PlanStep]:
    """Plan for traffic spikes, DDoS, and resource pressure events."""
    context = state.get("context", {})
    namespace = context.get("namespace", "iotag-dev")
    pod = context.get("pod", "")
    service = context.get("service", "") or context.get("app", "") or (pod.rsplit("-", 2)[0] if pod else "")
    repo = context.get("repo_full_name", "")

    plan: list[PlanStep] = [
        # 1. Confirm spike is still active
        {
            "tool": "prometheus",
            "action": "query",
            "args": {
                "query": f'sum(rate(http_server_requests_seconds_count{{namespace="{namespace}"}}[1m])) by (pod)',
            },
        },
        # 2. Current replica count and pod health
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
        # 3. Loki errors during spike
        {
            "tool": "loki",
            "action": "query_range",
            "args": {
                "namespace": namespace,
                "service": service,
                "last_minutes": 5,
                "limit": 100,
            },
        },
    ]

    # 4. If we know the repo, fetch current values to propose a fix
    if repo:
        plan.append({
            "tool": "github",
            "action": "get_file_content",
            "args": {"repo": repo, "path": "Helm/values-dev.yaml", "ref": "main"},
        })

    return plan


def _loki_error_plan(state: AgentState, request: str) -> list[PlanStep]:
    """Plan for loki_error_spike events — identify service and check K8s state."""
    context = state.get("context", {})
    namespace = context.get("namespace", "iotag-dev")
    app = context.get("app", "") or context.get("service", "")

    return [
        {
            "tool": "loki",
            "action": "query_range",
            "args": {
                "namespace": namespace,
                "service": app,
                "last_minutes": 10,
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
    """Plan for github_issue / github_pr events — fetch real Helm + CI files + live K8s context."""
    context = state.get("context", {})
    repo = context.get("repo_full_name", "")
    namespace = context.get("namespace", "iotag-dev")
    branch = (
        context.get("branch")
        or (context.get("pr") or {}).get("head", {}).get("ref", "")
        or "main"
    )
    plan: list[PlanStep] = [
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
        # Live K8s state — confirms whether pods are actually crashing / healthy
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

    # Loki logs for the service mentioned in the issue (e.g. CrashLoopBackOff / HikariPool errors)
    service = _infer_service_from_repo(repo)
    if service:
        plan.append({
            "tool": "loki",
            "action": "query_range",
            "args": {
                "namespace": namespace,
                "service": service,
                "last_minutes": 30,
                "limit": 150,
            },
        })

    return plan


def _quota_exceeded_plan(state: AgentState, request: str) -> list[PlanStep]:
    """Plan for namespace_quota_exceeded events.

    Collects:
    1. Current ResourceQuota usage (which resource type is exhausted).
    2. All pods — identify top consumers by count.
    3. Prometheus top memory/CPU consumers — pinpoint over-requesting services.
    4. Helm values from GitHub — fetch current resource requests to propose a fix.
    """
    context = state.get("context", {})
    namespace = context.get("namespace", "iotag-dev")
    repo = context.get("repo_full_name", "")

    plan: list[PlanStep] = [
        {
            "tool": "kubernetes",
            "action": "get_resource_quota",
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
        {
            "tool": "prometheus",
            "action": "query",
            "args": {
                "query": (
                    f'sort_desc(sum(container_memory_working_set_bytes{{namespace="{namespace}",container!=""}}) by (pod))'
                ),
            },
        },
        {
            "tool": "prometheus",
            "action": "query",
            "args": {
                "query": (
                    f'sort_desc(sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",container!=""}}[5m])) by (pod))'
                ),
            },
        },
    ]

    if repo:
        plan.append({
            "tool": "github",
            "action": "get_file_content",
            "args": {"repo": repo, "path": "Helm/values-dev.yaml", "ref": "main"},
        })

    return plan


def _disk_pressure_plan(state: AgentState, request: str) -> list[PlanStep]:
    """Plan for pvc_disk_pressure and node_disk_pressure events.

    Collects:
    1. PVC list with capacity/phase.
    2. Node conditions (DiskPressure, MemoryPressure).
    3. Prometheus storage metrics — PVC fill rate trend.
    4. Pod logs from the affected service — look for write-error messages.
    5. Kubernetes events — scheduling / eviction messages.
    """
    context = state.get("context", {})
    namespace = context.get("namespace", "iotag-dev")
    pvc = context.get("pvc", "")
    node = context.get("node", "")
    repo = context.get("repo_full_name", "")

    plan: list[PlanStep] = [
        {
            "tool": "kubernetes",
            "action": "get_pvc_usage",
            "args": {"namespace": namespace},
        },
        {
            "tool": "kubernetes",
            "action": "get_node_conditions",
            "args": {},
        },
        {
            "tool": "kubernetes",
            "action": "get_events",
            "args": {"namespace": namespace},
        },
        # PVC fill-rate trend (last 30 min)
        {
            "tool": "prometheus",
            "action": "query_range",
            "args": {
                "query": (
                    f'kubelet_volume_stats_used_bytes{{namespace="{namespace}"}}'
                    f' / kubelet_volume_stats_capacity_bytes{{namespace="{namespace}"}}'
                ),
                "last_minutes": 30,
            },
        },
        # Node filesystem free space
        {
            "tool": "prometheus",
            "action": "query",
            "args": {
                "query": (
                    'min by (instance, mountpoint) ('
                    '  node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|devtmpfs"}'
                    '  / node_filesystem_size_bytes{fstype!~"tmpfs|overlay|devtmpfs"}'
                    ')'
                ),
            },
        },
    ]

    # Loki logs for write errors on the affected service
    service = context.get("service", "")
    if not service and pvc:
        # Derive service name from PVC naming convention (e.g. data-iot-agent-logs-0 → iot-agent-logs)
        service = pvc.rsplit("-", 1)[0] if "-" in pvc else pvc
    if service:
        plan.append({
            "tool": "loki",
            "action": "query_range",
            "args": {
                "namespace": namespace,
                "service": service,
                "last_minutes": 15,
                "limit": 100,
            },
        })

    if repo:
        plan.append({
            "tool": "github",
            "action": "get_file_content",
            "args": {"repo": repo, "path": "Helm/values-dev.yaml", "ref": "main"},
        })

    return plan


def _oomkilled_plan(state: AgentState, request: str) -> list[PlanStep]:
    """Plan for k8s_pod_oomkilled events.

    Collects:
    1. Full pod describe — confirms OOMKilled exit code, current memory limit.
    2. JVM heap usage trend (30 min) from Prometheus — distinguishes leak vs low limit.
    3. Pod logs (last 200 lines) — look for OutOfMemoryError / GC overhead messages.
    4. Cluster events — shows OOMKilling reason message.
    5. Helm values from GitHub — contains current limits.memory to propose a fix.
    """
    context = state.get("context", {})
    namespace = context.get("namespace", "iotag-dev")
    pod_info = context.get("pod", {})
    pod_name = pod_info.get("name", "") if isinstance(pod_info, dict) else str(pod_info)
    repo = context.get("repo_full_name", "")

    # Infer service name from pod name (strip replica-set suffix: name-<rs>-<pod>)
    parts = pod_name.rsplit("-", 2)
    service = parts[0] if len(parts) >= 3 else pod_name

    plan: list[PlanStep] = [
        {
            "tool": "kubernetes",
            "action": "describe_pod",
            "args": {"pod_name": pod_name, "namespace": namespace},
        },
        {
            "tool": "kubernetes",
            "action": "get_pod_logs",
            "args": {"namespace": namespace, "pod": pod_name, "tail": 200},
        },
        {
            "tool": "kubernetes",
            "action": "get_events",
            "args": {"namespace": namespace},
        },
        # JVM heap trend — critical to distinguish memory leak from underprovisioned limit
        {
            "tool": "prometheus",
            "action": "query_range",
            "args": {
                "query": (
                    f'jvm_memory_used_bytes{{area="heap",namespace="{namespace}"}}'
                    f' / jvm_memory_max_bytes{{area="heap",namespace="{namespace}"}}'
                ),
                "last_minutes": 30,
            },
        },
        # Container memory working set trend
        {
            "tool": "prometheus",
            "action": "query_range",
            "args": {
                "query": (
                    f'container_memory_working_set_bytes{{namespace="{namespace}",'
                    f'pod=~"{service}.*",container!=""}}'
                ),
                "last_minutes": 30,
            },
        },
    ]

    if repo:
        plan.append({
            "tool": "github",
            "action": "get_file_content",
            "args": {"repo": repo, "path": "Helm/values-dev.yaml", "ref": "main"},
        })

    return plan


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
    elif event_kind in ("request_rate_spike", "high_cpu_usage", "high_http_latency", "high_error_rate"):
        plan = _scalability_plan(state, request)
    elif event_kind == "loki_error_spike":
        plan = _loki_error_plan(state, request)
    elif event_kind in ("k8s_orphaned_resource",):
        plan = _orphaned_resource_plan(state, request)
    elif event_kind in ("istio_vs_misconfiguration",):
        plan = _kubernetes_plan(state, request)
    elif event_kind == "namespace_quota_exceeded":
        plan = _quota_exceeded_plan(state, request)
    elif event_kind in ("pvc_disk_pressure", "node_disk_pressure"):
        plan = _disk_pressure_plan(state, request)
    elif event_kind == "k8s_pod_oomkilled":
        plan = _oomkilled_plan(state, request)
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
