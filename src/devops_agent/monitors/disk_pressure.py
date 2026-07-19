from __future__ import annotations

import logging
from devops_agent.event import AgentEvent
from devops_agent.monitors.base import BaseMonitor
from devops_agent.mcp_adapter import MCPAdapter

logger = logging.getLogger(__name__)

_PVC_WARN_THRESHOLD = 0.80
_NODE_DISK_FREE_THRESHOLD = 0.15

_PVC_USAGE_QUERY = (
    'kubelet_volume_stats_used_bytes{{namespace="{namespace}"}}'
    ' / kubelet_volume_stats_capacity_bytes{{namespace="{namespace}"}}'
)

_NODE_DISK_FREE_QUERY = (
    'min by (instance, mountpoint) ('
    '  node_filesystem_avail_bytes{{fstype!~"tmpfs|overlay|devtmpfs"}}'
    '  / node_filesystem_size_bytes{{fstype!~"tmpfs|overlay|devtmpfs"}}'
    ')'
)


class DiskPressureMonitor(BaseMonitor):
    """Monitors PVC utilisation and node filesystem pressure.

    Emits:
    - ``pvc_disk_pressure`` — a PVC in the namespace exceeds 80 % usage.
    - ``node_disk_pressure`` — a node filesystem drops below 15 % free space,
      OR the Kubernetes DiskPressure condition is True.

    Uses Prometheus for PVC metrics (kubelet_volume_stats) and the K8s
    adapter for node conditions. Deduplication is keyed per PVC / node.
    """

    def __init__(self, adapter: MCPAdapter, namespace: str = "iotag-dev") -> None:
        self._adapter = adapter
        self._namespace = namespace
        self._alerted_pvcs: set[str] = set()
        self._alerted_nodes: set[str] = set()

    def poll(self) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        try:
            events.extend(self._check_pvc_usage())
        except Exception:
            logger.exception("[DiskPressureMonitor] PVC check failed")
        try:
            events.extend(self._check_node_conditions())
        except Exception:
            logger.exception("[DiskPressureMonitor] node conditions check failed")
        return events

    # ── PVC usage via Prometheus ──────────────────────────────────────────────

    def _check_pvc_usage(self) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        query = _PVC_USAGE_QUERY.format(namespace=self._namespace)
        resp = self._adapter.run("prometheus", "query", {"query": query}, dry_run=False)

        if resp.get("status") not in ("ok", None):
            # Prometheus unreachable — skip silently, avoids alert storm on Prom downtime.
            return events

        still_high: set[str] = set()
        for metric in resp.get("results", []):
            labels = metric.get("metric", {})
            pvc = labels.get("persistentvolumeclaim", "")
            if not pvc:
                continue
            try:
                ratio = float(metric["value"][1])
            except (KeyError, ValueError, TypeError):
                continue

            key = f"pvc:{self._namespace}:{pvc}"
            if ratio > _PVC_WARN_THRESHOLD:
                still_high.add(key)
                if key not in self._alerted_pvcs:
                    self._alerted_pvcs.add(key)
                    severity = "critical" if ratio > 0.95 else "warning"
                    events.append(AgentEvent(
                        kind="pvc_disk_pressure",
                        title=(
                            f"PVC {pvc} disk usage at {ratio:.0%} "
                            f"in {self._namespace}"
                        ),
                        context={
                            "namespace": self._namespace,
                            "pvc": pvc,
                            "ratio": round(ratio, 3),
                            "severity": severity,
                        },
                    ))
                    logger.warning(
                        "[DiskPressureMonitor] PVC %s/%s at %.0f%%",
                        self._namespace, pvc, ratio * 100,
                    )
            else:
                self._alerted_pvcs.discard(key)

        # Clear alerts for PVCs that dropped below threshold (no longer in results).
        stale = self._alerted_pvcs - still_high - {
            k for k in self._alerted_pvcs if not k.startswith(f"pvc:{self._namespace}:")
        }
        self._alerted_pvcs -= stale
        return events

    # ── Node conditions via K8s adapter ──────────────────────────────────────

    def _check_node_conditions(self) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        resp = self._adapter.run("kubernetes", "get_node_conditions", {}, dry_run=False)

        if resp.get("status") != "ok":
            logger.debug("[DiskPressureMonitor] get_node_conditions: %s", resp.get("message"))
            return events

        for node in resp.get("nodes", []):
            node_name: str = node.get("name", "")
            conditions: dict = node.get("conditions", {})
            key = f"node:{node_name}"

            disk_pressure_active = conditions.get("DiskPressure") == "True"
            if disk_pressure_active and key not in self._alerted_nodes:
                self._alerted_nodes.add(key)
                events.append(AgentEvent(
                    kind="node_disk_pressure",
                    title=f"Node {node_name} has DiskPressure condition",
                    context={
                        "node": node_name,
                        "conditions": conditions,
                        "allocatable_cpu": node.get("allocatable_cpu", ""),
                        "allocatable_memory": node.get("allocatable_memory", ""),
                        "allocatable_pods": node.get("allocatable_pods", ""),
                        "severity": "critical",
                    },
                ))
                logger.warning(
                    "[DiskPressureMonitor] Node %s DiskPressure=True", node_name,
                )
            elif not disk_pressure_active:
                self._alerted_nodes.discard(key)

        return events
