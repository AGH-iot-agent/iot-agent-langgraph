from __future__ import annotations
import csv
import io
import logging
import os
import time
from uuid import uuid4
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
import prometheus_client

from devops_agent.graph import build_graph
from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.watchdog import AgentWatchdog, WatchdogConfig
from devops_agent.monitors import GitHubIssueMonitor, K3sHealthMonitor, GitHubPRMonitor, GitHubCIFailureMonitor, GitHubPRBuildMonitor, PrometheusMetricsMonitor, LokiErrorMonitor, ResourceQuotaMonitor, DiskPressureMonitor
from devops_agent.metrics import agent_metrics, load_manual_baselines, record_security_violation
from devops_agent.preflight import log_runtime_preflight, runtime_preflight_report
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

load_dotenv(".env") 

class RunRequest(BaseModel):
    request: str = Field(..., min_length=3)
    dry_run: bool = False
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

FORCE_BOOT_AUTOSTART = _flag("IOTAG_FORCE_BOOT_AUTOSTART", default=True)
FORCE_BOOT_MONITOR = _flag("IOTAG_FORCE_BOOT_MONITOR", default=True)

AUTOAPPLY_ENABLED = _flag("AGENT_AUTOAPPLY", default=False)
if AUTOAPPLY_ENABLED and (KUBERNETES_NAMESPACE or "") != "iotag-dev":
    logger.error(
        "AGENT_AUTOAPPLY=true is only allowed when KUBERNETES_NAMESPACE=iotag-dev. Current namespace=%s",
        KUBERNETES_NAMESPACE,
    )

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

if FORCE_BOOT_MONITOR and not monitors and ORG_NAME:
    logger.warning(
        "No monitors enabled; forcing GitHub issue monitor for bootstrap mode."
    )
    monitors.append(GitHubIssueMonitor(adapter=mcp_adapter, org=ORG_NAME))
    active_monitors.append("github_issues_forced_boot")

logger.info("Active monitors: %s", active_monitors)

watchdog = AgentWatchdog(
    adapter  = mcp_adapter,
    graph    = compiled_graph,
    monitors = monitors,
)

runtime_preflight = runtime_preflight_report()
log_runtime_preflight(runtime_preflight)

@asynccontextmanager
async def lifespan(app: FastAPI):
    autostart_enabled = _flag("IOTAG_WATCHDOG_AUTOSTART", default=True)
    if autostart_enabled or FORCE_BOOT_AUTOSTART:
        if not autostart_enabled and FORCE_BOOT_AUTOSTART:
            logger.warning(
                "IOTAG_WATCHDOG_AUTOSTART is false, but bootstrap mode forces autostart."
            )
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


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    trace_id = request.headers.get("x-trace-id") or f"http-{uuid4()}"
    request.state.trace_id = trace_id
    started_at = time.time()
    logger.info(
        "[HTTP] start method=%s path=%s trace_id=%s",
        request.method,
        request.url.path,
        trace_id,
    )
    try:
        response = await call_next(request)
        latency_ms = round((time.time() - started_at) * 1000.0, 2)
        response.headers["X-Trace-Id"] = trace_id
        logger.info(
            "[HTTP] end method=%s path=%s status=%s latency_ms=%s trace_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            latency_ms,
            trace_id,
        )
        return response
    except Exception:
        logger.exception(
            "[HTTP] unhandled method=%s path=%s trace_id=%s",
            request.method,
            request.url.path,
            trace_id,
        )
        raise


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    trace_id = getattr(request.state, "trace_id", f"validation-{uuid4()}")
    logger.warning(
        "[HTTP] validation_error path=%s trace_id=%s errors=%s",
        request.url.path,
        trace_id,
        exc.errors(),
    )
    return JSONResponse(
        status_code=422,
        content={"status": "error", "trace_id": trace_id, "detail": exc.errors()},
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    trace_id = getattr(request.state, "trace_id", f"http-exc-{uuid4()}")
    logger.error(
        "[HTTP] http_exception path=%s status=%s trace_id=%s detail=%s",
        request.url.path,
        exc.status_code,
        trace_id,
        exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "trace_id": trace_id, "detail": exc.detail},
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", f"unhandled-{uuid4()}")
    logger.exception(
        "[HTTP] fatal_exception path=%s trace_id=%s",
        request.url.path,
        trace_id,
    )
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "trace_id": trace_id,
            "detail": "Internal server error. Check logs with this trace_id.",
        },
    )

def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    try:
        app.openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        return app.openapi_schema
    except Exception:
        logger.exception("[OPENAPI] Failed to generate OpenAPI schema")
        raise


app.openapi = _custom_openapi

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
def health() -> dict[str, Any]:
    return {
        "status": "ok" if runtime_preflight["ok"] else "degraded",
        "runtime_preflight": runtime_preflight,
    }

@app.post("/run")
def run_agent(payload: RunRequest) -> dict:
    logger.info(
        "Run agent: request=%s dry_run=%s max_revisions=%s",
        payload.request, payload.dry_run, payload.max_revisions,
    )

    trace_id = str(payload.context.get("trace_id") or f"manual-{uuid4()}")
    run_context = dict(payload.context)
    run_context["trace_id"] = trace_id

    initial_state = {
        "trace_id": trace_id,
        "request": payload.request,
        "dry_run": payload.dry_run,
        "revision_count": 0,
        "max_revisions": payload.max_revisions,
        "context": run_context,
        "started_at": time.time(),
    }
    started_at = time.time()
    try:
        result = compiled_graph.invoke(initial_state)
    except Exception as exc:
        logger.exception("Run agent failed: trace_id=%s", trace_id)
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Agent execution failed",
                "trace_id": trace_id,
                "error": str(exc),
            },
        )
    logger.info("Run agent result: %s", result)

    security_violations = result.get("security_violations") or []
    security_blocked = bool(result.get("security_blocked", False))
    if security_violations:
        logger.warning(
            "[SECURITY] %d violation(s) recorded during run. blocked=%s",
            len(security_violations), security_blocked,
        )
        _record_security_prom(security_violations)

    response = dict(result)
    response["security"] = {
        "blocked": security_blocked,
        "violation_count": len(security_violations),
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
            namespace=payload.namespace,
            interval_seconds=payload.interval_seconds,
            monitor_github=payload.monitor_github,
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


@app.get("/metrics", response_class=Response)
def metrics_prometheus() -> Response:
    """Metryki Prometheus: tokeny, MTTR, liczba planów, naruszenia bezpieczeństwa.

    Endpoint scraped przez Prometheus / ServiceMonitor.
    Content-Type: text/plain; version=0.0.4; charset=utf-8
    """
    data = prometheus_client.generate_latest()
    return Response(
        content=data,
        media_type=prometheus_client.CONTENT_TYPE_LATEST,
    )


@app.get("/alerts")
def alerts(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    """Lista aktywnych zdarzeń emitowanych przez monitory (wymagana przez tabelę 4.2)."""
    alert_list = watchdog.recent_alerts(limit=limit)
    logger.info("GET /alerts: %d alerts returned", len(alert_list))
    return {"status": "ok", "count": len(alert_list), "alerts": alert_list}


# Hook: rejestruj naruszenia bezpieczeństwa w Prometheus po każdym /run
def _record_security_prom(security_violations: list[dict[str, str]]) -> None:
    for v in security_violations:
        threat = v.get("threat_type", "unknown")
        record_security_violation(threat)
