from __future__ import annotations

import logging
from devops_agent.monitors.base import BaseMonitor
from devops_agent.event import AgentEvent

logger = logging.getLogger(__name__)

ERROR_COUNT_THRESHOLD_GLOBAL = 10   # total errors across namespace
ERROR_COUNT_THRESHOLD_SERVICE = 20  # errors per individual service
LOOK_BACK_MINUTES = 10


class LokiErrorMonitor(BaseMonitor):
    """Polls Loki for error-level log lines and fires AgentEvents when the
    count exceeds the threshold within the last LOOK_BACK_MINUTES.

    Fires per-service events (keyed by app label) so the agent can identify
    which service is producing errors rather than a single namespace-level alert.
    """

    def __init__(self, adapter, namespace: str = "iotag-dev") -> None:
        self._adapter   = adapter
        self._namespace = namespace
        self._alerted: set[str] = set()

    def poll(self) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        events.extend(self._check_per_service_errors())
        return events

    def _check_per_service_errors(self) -> list[AgentEvent]:
        """Query Loki for ERROR-level lines grouped by app label."""
        logql = (
            f'{{namespace="{self._namespace}"}} '
            f'|~ "(?i)(error|exception|fatal)" '
            f'| logfmt | level=~"(error|ERROR|fatal|FATAL)"'
        )
        result = self._adapter.run(
            "loki",
            "query_range",
            {
                "query":        logql,
                "last_minutes": LOOK_BACK_MINUTES,
                "limit":        500,
            },
            dry_run=False,
        )

        if result.get("status") != "ok":
            logger.warning("[LOKI MONITOR] query failed: %s", result.get("message"))
            return []

        lines: list[str] = result.get("lines", [])
        streams: list[dict] = result.get("streams", [])

        service_lines: dict[str, list[str]] = {}
        for stream in streams:
            labels = stream.get("labels") or stream.get("stream") or {}
            app = labels.get("app") or labels.get("container") or labels.get("pod", "unknown")
            service_lines.setdefault(app, []).extend(stream.get("values", []))

        if not service_lines and lines:
            service_lines["_namespace"] = lines

        events: list[AgentEvent] = []

        for app, app_lines in service_lines.items():
            count = len(app_lines)
            threshold = ERROR_COUNT_THRESHOLD_SERVICE if app != "_namespace" else ERROR_COUNT_THRESHOLD_GLOBAL
            key = f"loki_error_spike:{self._namespace}:{app}"

            if count >= threshold:
                if key not in self._alerted:
                    self._alerted.add(key)
                    snippet = "\n".join(str(l) for l in app_lines[:10])
                    logger.info(
                        "[LOKI MONITOR] Error spike — namespace=%s app=%s: %d lines",
                        self._namespace, app, count,
                    )
                    events.append(AgentEvent(
                        kind  = "loki_error_spike",
                        title = (
                            f"Error log spike in {self._namespace}/{app}: "
                            f"{count} error lines in last {LOOK_BACK_MINUTES}m"
                        ),
                        context={
                            "namespace":    self._namespace,
                            "app":          app,
                            "service":      app,
                            "error_count":  count,
                            "threshold":    threshold,
                            "look_back_m":  LOOK_BACK_MINUTES,
                            "sample_lines": snippet,
                        },
                    ))
            else:
                self._alerted.discard(key)

        return events

