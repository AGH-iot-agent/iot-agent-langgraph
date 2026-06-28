from __future__ import annotations

import logging
from devops_agent.event import AgentEvent
from devops_agent.monitors.base import BaseMonitor
from devops_agent.mcp_adapter import MCPAdapter

logger = logging.getLogger(__name__)

# Warn when any quota resource exceeds this fraction of its hard limit.
_QUOTA_WARN_THRESHOLD = 0.80


def _parse_quantity(raw: str) -> float | None:
    """Convert a Kubernetes resource quantity string to a float for comparison.

    Supports:
      - plain integers / floats: "10", "1.5"
      - milli-CPU notation: "500m" → 0.5
      - memory suffixes: Ki, Mi, Gi, Ti (binary) and K, M, G, T (decimal)
    Returns None when the value cannot be parsed (e.g. empty string).
    """
    if not raw:
        return None
    raw = raw.strip()
    try:
        return float(raw)
    except ValueError:
        pass
    if raw.endswith("m"):
        try:
            return float(raw[:-1]) / 1000.0
        except ValueError:
            return None
    _binary = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}
    _decimal = {"K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12}
    for suffix, multiplier in {**_binary, **_decimal}.items():
        if raw.endswith(suffix):
            try:
                return float(raw[: -len(suffix)]) * multiplier
            except ValueError:
                return None
    return None


class ResourceQuotaMonitor(BaseMonitor):
    """Monitors Kubernetes ResourceQuota utilisation in a namespace.

    Emits a ``namespace_quota_exceeded`` event when any quota resource
    usage exceeds 80 % of its hard limit. Separate deduplication keys
    are tracked per (namespace, resource) pair and cleared once usage
    drops back below the threshold.
    """

    def __init__(self, adapter: MCPAdapter, namespace: str = "iotag-dev") -> None:
        self._adapter = adapter
        self._namespace = namespace
        # Keys: "{namespace}:{resource_name}" — set while alerted to avoid duplicates.
        self._alerted: set[str] = set()

    def poll(self) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        try:
            resp = self._adapter.run(
                "kubernetes", "get_resource_quota",
                {"namespace": self._namespace},
                dry_run=False,
            )
            if resp.get("status") != "ok":
                logger.warning("[ResourceQuotaMonitor] get_resource_quota error: %s", resp.get("message"))
                return events

            for quota in resp.get("quotas", []):
                events.extend(self._check_quota(quota))

        except Exception:
            logger.exception("[ResourceQuotaMonitor] poll failed")
        return events

    def _check_quota(self, quota: dict) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        quota_name = quota.get("name", "default")
        for resource in quota.get("resources", []):
            resource_name: str = resource.get("resource", "")
            hard_raw: str = resource.get("hard", "")
            used_raw: str = resource.get("used", "0")

            hard = _parse_quantity(hard_raw)
            used = _parse_quantity(used_raw)

            if hard is None or used is None or hard == 0:
                continue

            ratio = used / hard
            key = f"{self._namespace}:{resource_name}"

            if ratio > _QUOTA_WARN_THRESHOLD and key not in self._alerted:
                self._alerted.add(key)
                severity = "critical" if ratio >= 1.0 else "warning"
                events.append(AgentEvent(
                    kind="namespace_quota_exceeded",
                    title=(
                        f"Quota {resource_name} at {ratio:.0%} in {self._namespace} "
                        f"({used_raw} / {hard_raw})"
                    ),
                    context={
                        "namespace": self._namespace,
                        "quota_name": quota_name,
                        "resource": resource_name,
                        "used": used_raw,
                        "hard": hard_raw,
                        "ratio": round(ratio, 3),
                        "severity": severity,
                    },
                ))
                logger.warning(
                    "[ResourceQuotaMonitor] %s quota '%s' at %.0f%% (%s/%s)",
                    self._namespace, resource_name, ratio * 100, used_raw, hard_raw,
                )
            elif ratio <= _QUOTA_WARN_THRESHOLD:
                self._alerted.discard(key)

        return events
