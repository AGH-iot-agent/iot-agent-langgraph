from __future__ import annotations
import logging
from devops_agent.event import AgentEvent
from devops_agent.monitors.base import BaseMonitor
from devops_agent.mcp_adapter import MCPAdapter

logger = logging.getLogger(__name__)

class K3sHealthMonitor(BaseMonitor):
    def __init__(self, adapter: MCPAdapter, namespace: str = "iot-agent") -> None:
        self._adapter = adapter
        self._namespace = namespace
        self._alerted: set[str] = set()

    def poll(self) -> list[AgentEvent]:
        events = []
        try:
            pods_resp = self._adapter.run("kubernetes", "get_pods", {"namespace": self._namespace}, dry_run=False)
            pods = pods_resp.get("pods", []) or []
            rollout_resp = self._adapter.run("kubernetes", "get_rollout_status", {"namespace": self._namespace}, dry_run=False)
            deployments = rollout_resp.get("deployments", []) or []
            events_resp = self._adapter.run("kubernetes", "get_events", {"namespace": self._namespace}, dry_run=False)
            k8s_events = events_resp.get("events", []) or []
            snapshot = {"pods": pods, "deployments": deployments, "events": k8s_events}
            healthy_now = set()
            for pod in pods:
                name = pod.get("name")
                phase = str(pod.get("phase", "Unknown"))
                restarts = int(pod.get("restarts", 0))
                ready = bool(pod.get("ready", False))
                is_unhealthy = phase not in {"Running", "Succeeded"} or not ready or restarts > 3
                if is_unhealthy:
                    if name not in self._alerted:
                        events.append(AgentEvent(
                            kind="k8s_pod_crash",
                            title=f"Pod unhealthy: {name} (phase={phase}, restarts={restarts})",
                            context={
                                "namespace": self._namespace,
                                "pod": pod,
                                "cluster_snapshot": snapshot,
                            }
                        ))
                        self._alerted.add(name)
                else:
                    healthy_now.add(name)
            # Remove pods that became healthy
            self._alerted -= healthy_now
        except Exception:
            logger.exception("[K3sHealthMonitor] poll failed")
        return events
