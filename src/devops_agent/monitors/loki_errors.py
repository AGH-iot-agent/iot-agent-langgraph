from __future__ import annotations

import logging
from devops_agent.monitors.base import BaseMonitor
from devops_agent.event import AgentEvent

logger = logging.getLogger(__name__)

ERROR_COUNT_THRESHOLD = 10 
LOOK_BACK_MINUTES     = 5


class LokiErrorMonitor(BaseMonitor):
    """Polls Loki for error-level log lines and fires AgentEvents when the
    count exceeds ERROR_COUNT_THRESHOLD within the last LOOK_BACK_MINUTES."""

    def __init__(self, adapter, namespace: str = "iotag-dev") -> None:
        self._adapter   = adapter
        self._namespace = namespace
        self._alerted: set[str] = set()

    def poll(self) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        events.extend(self._check_error_logs())
        return events

    def _check_error_logs(self) -> list[AgentEvent]:
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
        count = len(lines)

        key = f"loki_error_spike:{self._namespace}"
        if count >= ERROR_COUNT_THRESHOLD:
            if key not in self._alerted:
                self._alerted.add(key)
                snippet = "\n".join(lines[:10])
                logger.info(
                    "[LOKI MONITOR] Error spike in namespace=%s: %d lines",
                    self._namespace, count,
                )
                return [AgentEvent(
                    kind  = "loki_error_spike",
                    title = (
                        f"Error log spike in {self._namespace}: "
                        f"{count} error lines in last {LOOK_BACK_MINUTES}m"
                    ),
                    context={
                        "namespace":    self._namespace,
                        "error_count":  count,
                        "threshold":    ERROR_COUNT_THRESHOLD,
                        "look_back_m":  LOOK_BACK_MINUTES,
                        "sample_lines": snippet,
                    },
                )]
        else:
            self._alerted.discard(key)

        return []
