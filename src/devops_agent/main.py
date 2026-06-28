from __future__ import annotations
import csv
import io
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from devops_agent.graph import build_graph
from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.watchdog import AgentWatchdog, WatchdogConfig
from devops_agent.monitors import GitHubIssueMonitor, K3sHealthMonitor, GitHubPRMonitor, GitHubCIFailureMonitor, GitHubPRBuildMonitor, PrometheusMetricsMonitor, LokiErrorMonitor, ResourceQuotaMonitor, DiskPressureMonitor
from devops_agent.metrics import agent_metrics, load_manual_baselines
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv(".env") 

class RunRequest(BaseModel):
    request: str = Field(..., min_length=3)
    dry_run: bool = True
    max_revisions: int = 2
    context: dict[str, Any] = Field(default_factory=dict)

class WatchStartRequest(BaseModel):
    namespace: str = Field(default="iot-agent", min_length=1)
    interval_seconds: int = Field(default=30, ge=5, le=3600)
    monitor_github: bool = True

mcp_adapter = MCPAdapter()
compiled_graph = build_graph()

ORG_NAME            = os.environ.get("GITHUB_ORG")
KUBERNETES_NAMESPACE = os.environ.get("KUBERNETES_NAMESPACE")
INTERVAL_SECONDS    = int(os.environ.get("WATCHDOG_INTERVAL_SECONDS", "30"))

def _flag(name: str, default: bool = True) -> bool:
    """Read a boolean feature flag from env. Accepts true/false/1/0/yes/no."""
    val = os.environ.get(name, "true" if default else "false").lower().strip()
    return val in ("true", "1", "yes")

MONITOR_FLAGS = {
    "github_issues":    _flag("MONITOR_GITHUB_ISSUES"),
    "github_prs":       _flag("MONITOR_GITHUB_PRS"),
    "github_ci":        _flag("MONITOR_GITHUB_CI_FAILURE"),
    "github_pr_builds": _flag("MONITOR_GITHUB_PR_BUILDS"),
    "prometheus":       _flag("MONITOR_PROMETHEUS"),
    "loki":             _flag("MONITOR_LOKI"),
    "k8s":              _flag("MONITOR_K8S"),
}

monitors = []
active_monitors: list[str] = []

if not ORG_NAME:
    logger.error("GITHUB_ORG not set — all GitHub monitors disabled.")
    ORG_NAME = None
else:
    if MONITOR_FLAGS["github_issues"]:
        monitors.append(GitHubIssueMonitor(adapter=mcp_adapter, org=ORG_NAME))
        active_monitors.append("github_issues")
    if MONITOR_FLAGS["github_prs"]:
        monitors.append(GitHubPRMonitor(adapter=mcp_adapter, org=ORG_NAME))
        active_monitors.append("github_prs")
    if MONITOR_FLAGS["github_ci"]:
        monitors.append(GitHubCIFailureMonitor(adapter=mcp_adapter, org=ORG_NAME))
        active_monitors.append("github_ci_failure")
    if MONITOR_FLAGS["github_pr_builds"]:
        monitors.append(GitHubPRBuildMonitor(adapter=mcp_adapter, org=ORG_NAME))
        active_monitors.append("github_pr_builds")
    if MONITOR_FLAGS["prometheus"]:
        monitors.append(PrometheusMetricsMonitor(adapter=mcp_adapter, namespace=KUBERNETES_NAMESPACE or "iot-agent"))
        active_monitors.append("prometheus_metrics")
    if MONITOR_FLAGS["loki"]:
        monitors.append(LokiErrorMonitor(adapter=mcp_adapter, namespace=KUBERNETES_NAMESPACE or "iot-agent"))
        active_monitors.append("loki_errors")

if not KUBERNETES_NAMESPACE:
    logger.error("KUBERNETES_NAMESPACE not set — k8s monitor disabled.")
    KUBERNETES_NAMESPACE = None
elif MONITOR_FLAGS["k8s"]:
    monitors.append(K3sHealthMonitor(adapter=mcp_adapter, namespace=KUBERNETES_NAMESPACE))
    active_monitors.append("k8s")
    monitors.append(ResourceQuotaMonitor(adapter=mcp_adapter, namespace=KUBERNETES_NAMESPACE))
    active_monitors.append("resource_quota")
    monitors.append(DiskPressureMonitor(adapter=mcp_adapter, namespace=KUBERNETES_NAMESPACE))
    active_monitors.append("disk_pressure")

logger.info("Active monitors: %s", active_monitors)

watchdog = AgentWatchdog(
    adapter  = mcp_adapter,
    graph    = compiled_graph,
    monitors = monitors,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("IOTAG_WATCHDOG_AUTOSTART", "true").lower() == "true":
        cfg = WatchdogConfig(
            namespace                = KUBERNETES_NAMESPACE or "iotag-dev",
            interval_seconds         = INTERVAL_SECONDS,
            monitor_github           = ORG_NAME is not None,
            monitor_github_issues    = MONITOR_FLAGS["github_issues"],
            monitor_github_prs       = MONITOR_FLAGS["github_prs"],
            monitor_github_ci        = MONITOR_FLAGS["github_ci"],
            monitor_github_pr_builds = MONITOR_FLAGS["github_pr_builds"],
            monitor_prometheus       = MONITOR_FLAGS["prometheus"],
            monitor_loki             = MONITOR_FLAGS["loki"],
            monitor_k8s              = MONITOR_FLAGS["k8s"],
        )
        logger.info(
            "Autostart watchdog: namespace=%s interval=%ss active_monitors=%s",
            cfg.namespace, cfg.interval_seconds, active_monitors,
        )
        watchdog.start(cfg)

    yield

    logger.info("Shutting down watchdog…")
    watchdog.stop()

app = FastAPI(title="iot-agent-langgraph", version="0.1.0", lifespan=lifespan)

@app.get("/watch/monitors")
def watch_monitors() -> dict[str, Any]:
    """Returns which monitors are currently active and their feature flags."""
    return {
        "active": active_monitors,
        "flags": {
            "github_issues":    MONITOR_FLAGS["github_issues"],
            "github_prs":       MONITOR_FLAGS["github_prs"],
            "github_ci":        MONITOR_FLAGS["github_ci"],
            "github_pr_builds": MONITOR_FLAGS["github_pr_builds"],
            "k8s":              MONITOR_FLAGS["k8s"],
            "prometheus":       MONITOR_FLAGS["prometheus"],
            "loki":             MONITOR_FLAGS["loki"],
        },
        "env": {
            "org":       ORG_NAME,
            "namespace": KUBERNETES_NAMESPACE,
            "interval_seconds": INTERVAL_SECONDS,
        },
    }

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

@app.post("/run")
def run_agent(payload: RunRequest) -> dict:
    logger.info(
        "Run agent: request=%s dry_run=%s max_revisions=%s",
        payload.request, payload.dry_run, payload.max_revisions,
    )

    initial_state = {
        "request": payload.request,
        "dry_run": payload.dry_run,
        "revision_count": 0,
        "max_revisions": payload.max_revisions,
        "context": payload.context,
    }
    started_at = time.time()
    result = compiled_graph.invoke(initial_state)
    token_usage = result.get("token_usage") or {}
    input_tokens = int(token_usage.get("input_tokens", 0))
    output_tokens = int(token_usage.get("output_tokens", 0))
    plan = result.get("plan") or []
    validation = result.get("validation_result") or {}
    agent_metrics.record(
        {
            "trace_id": str(payload.context.get("trace_id", "manual-run")),
            "event_kind": str(result.get("event_kind", "manual_run")),
            "repo": str(payload.context.get("repo_full_name", "")),
            "title": payload.request,
            "mttr_s": time.time() - started_at,
            "plan_step_count": len(plan),
            "tool_call_rounds": int(token_usage.get("tool_call_rounds", 0)),
            "revision_count": int(result.get("revision_count", 0)),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "validation_passed": bool(validation.get("passed", False)),
            "pr_created": bool(result.get("pr_url")),
        }
    )
    logger.info("Run agent result: %s", result)

    # Surface security metadata to the caller without exposing raw violations
    security_violations = result.get("security_violations") or []
    security_blocked = bool(result.get("security_blocked", False))
    if security_violations:
        logger.warning(
            "[SECURITY] %d violation(s) recorded during run. blocked=%s",
            len(security_violations), security_blocked,
        )

    response = dict(result)
    response["security"] = {
        "blocked": security_blocked,
        "violation_count": len(security_violations),
        # Expose threat types only — never echo matched patterns back to caller
        "threat_types": list({v.get("threat_type") for v in security_violations}),
    }
    return response

@app.post("/watch/start")
def watch_start(payload: WatchStartRequest) -> dict[str, Any]:
    logger.info(
        "Watchdog start: namespace=%s interval=%s monitor_github=%s",
        payload.namespace, payload.interval_seconds, payload.monitor_github,
    )

    result = watchdog.start(
        WatchdogConfig(
            interval_seconds=INTERVAL_SECONDS,
            issue_max_age_s=86400,
        )
    )
    logger.info("Watchdog started: %s", result)
    return result

@app.post("/watch/stop")
def watch_stop() -> dict[str, Any]:
    logger.info("Watchdog stop requested")
    result = watchdog.stop()
    logger.info("Watchdog stopped: %s", result)
    return result

@app.get("/watch/status")
def watch_status() -> dict[str, Any]:
    status = watchdog.status()
    logger.info("Watchdog status: %s", status)
    return status

@app.get("/watch/alerts")
def watch_alerts(limit: int = 50) -> dict[str, Any]:
    alerts = watchdog.recent_alerts(limit=max(1, min(limit, 200)))
    logger.info("Watchdog alerts: %d alerts returned", len(alerts))
    return {"status": "ok", "alerts": alerts}


@app.get("/metrics/events")
def metrics_events(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    return {"status": "ok", "events": agent_metrics.list_events(limit=limit)}


@app.get("/metrics/summary")
def metrics_summary() -> dict[str, Any]:
    return {"status": "ok", "summary": agent_metrics.summary()}


@app.get("/metrics/adapters")
def metrics_adapters() -> dict[str, Any]:
    return {"status": "ok", "summary": agent_metrics.adapter_summary()}


@app.get("/metrics/comparison")
def metrics_comparison() -> dict[str, Any]:
    """Zestawia MTTR agenta z ręcznymi baseline'ami dla każdego scenariusza."""
    baselines = load_manual_baselines()
    events = agent_metrics.list_events(limit=500)

    rows: list[dict[str, Any]] = []
    for b in baselines.values():
        # Dopasuj event po tytule/kontekście jeśli możliwe; fallback: brak danych
        agent_mttr: float | None = None
        agent_tokens: int | None = None
        for ev in events:
            if b.scenario_id in ev.get("title", "") or b.name.split("(")[0].strip().lower() in ev.get("title", "").lower():
                agent_mttr = ev.get("mttr_s")
                agent_tokens = ev.get("total_tokens")
                break

        speedup = round(b.manual_mttr_s / agent_mttr, 2) if agent_mttr and agent_mttr > 0 else None
        rows.append({
            "scenario_id": b.scenario_id,
            "name": b.name,
            "category": b.category,
            "manual_mttr_s": b.manual_mttr_s,
            "manual_steps": b.manual_steps_count,
            "manual_resolver_role": b.manual_resolver_role,
            "agent_mttr_s": agent_mttr,
            "agent_total_tokens": agent_tokens,
            "speedup_factor": speedup,
        })

    measured = [r for r in rows if r["speedup_factor"] is not None]
    avg_speedup = round(sum(r["speedup_factor"] for r in measured) / len(measured), 2) if measured else None

    return {
        "status": "ok",
        "avg_speedup_factor": avg_speedup,
        "scenarios_with_agent_data": len(measured),
        "scenarios_total": len(rows),
        "rows": rows,
    }


@app.get("/metrics/export")
def metrics_export(format: str = Query(default="json", pattern="^(json|csv)$")) -> Any:
    """Eksportuje wszystkie zdarzenia z metrykami do JSON lub CSV (dla pracy magisterskiej)."""
    events = agent_metrics.list_events(limit=500)

    if format == "csv":
        if not events:
            return StreamingResponse(
                io.StringIO(""), media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=agent_metrics.csv"},
            )
        fieldnames = list(events[0].keys())
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(events)
        buf.seek(0)
        return StreamingResponse(
            buf, media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=agent_metrics.csv"},
        )

    return {"status": "ok", "count": len(events), "events": events}