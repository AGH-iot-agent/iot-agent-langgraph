from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from devops_agent.graph import build_graph
from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.watchdog import AgentWatchdog, WatchdogConfig


class RunRequest(BaseModel):
    request: str = Field(..., min_length=3)
    dry_run: bool = True
    max_revisions: int = 2
    context: dict[str, Any] = Field(default_factory=dict)


class WatchStartRequest(BaseModel):
    namespace: str = Field(default="iot-agent", min_length=1)
    interval_seconds: int = Field(default=30, ge=5, le=3600)
    monitor_github: bool = True


app = FastAPI(title="iot-agent-langgraph", version="0.1.0")
compiled_graph = build_graph()
watchdog = AgentWatchdog(MCPAdapter())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/run")
def run_agent(payload: RunRequest) -> dict:
    initial_state = {
        "request": payload.request,
        "dry_run": payload.dry_run,
        "revision_count": 0,
        "max_revisions": payload.max_revisions,
        "context": payload.context,
    }
    result = compiled_graph.invoke(initial_state)
    return result


@app.post("/watch/start")
def watch_start(payload: WatchStartRequest) -> dict[str, Any]:
    return watchdog.start(
        WatchdogConfig(
            namespace=payload.namespace,
            interval_seconds=payload.interval_seconds,
            monitor_github=payload.monitor_github,
        )
    )


@app.post("/watch/stop")
def watch_stop() -> dict[str, Any]:
    return watchdog.stop()


@app.get("/watch/status")
def watch_status() -> dict[str, Any]:
    return watchdog.status()


@app.get("/watch/alerts")
def watch_alerts(limit: int = 50) -> dict[str, Any]:
    return {
        "status": "ok",
        "alerts": watchdog.recent_alerts(limit=max(1, min(limit, 200))),
    }
