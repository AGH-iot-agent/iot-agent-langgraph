from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from devops_agent.mcp_adapter import MCPAdapter

logger = logging.getLogger(__name__)

@dataclass
class ComponentHealth:
    name: str
    ok: bool
    message: str = ""
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

@dataclass  
class HealthReport:
    components: list[ComponentHealth]
    
    @property
    def all_ok(self) -> bool:
        return all(c.ok for c in self.components)
    
    @property
    def unhealthy(self) -> list[str]:
        return [c.name for c in self.components if not c.ok]
    
    def to_dict(self) -> dict:
        return {
            "status": "ok" if self.all_ok else "degraded",
            "unhealthy": self.unhealthy,
            "components": [c.__dict__ for c in self.components],
        }

class InfraHealthChecker:
    def __init__(self, adapter: MCPAdapter, namespace: str = "iot-agent"):
        self._adapter  = adapter
        self._namespace = namespace

    def check_all(self) -> HealthReport:
        checks = [
            self._check_kubernetes(),
            self._check_prometheus(),
            self._check_loki(),
            self._check_github(),
        ]
        report = HealthReport(components=checks)
        
        if report.all_ok:
            logger.info("[HEALTH] All systems operational")
        else:
            logger.warning("[HEALTH] Degraded components: %s", report.unhealthy)
        
        return report

    def _check_kubernetes(self) -> ComponentHealth:
        r = self._adapter.run("kubernetes", "get_pods", {"namespace": self._namespace})
        ok = r.get("status") in {"ok", "degraded"}
        return ComponentHealth("kubernetes", ok, r.get("message", ""))

    def _check_prometheus(self) -> ComponentHealth:
        r = self._adapter.run("prometheus", "query", {"query": "up"})
        ok = r.get("status") in {"ok", "degraded"}
        return ComponentHealth("prometheus", ok, r.get("message", ""))

    def _check_loki(self) -> ComponentHealth:
        r = self._adapter.run("loki", "query_range", {
            "namespace": self._namespace,
            "service": "",
            "last_minutes": 1,
            "limit": 1,
        })
        ok = r.get("status") in {"ok", "degraded"}
        return ComponentHealth("loki", ok, r.get("message", ""))

    def _check_github(self) -> ComponentHealth:
        r = self._adapter.run("github", "list_org_repos", {})
        ok = r.get("status") in {"ok", "degraded"}
        return ComponentHealth("github", ok, r.get("message", ""))
