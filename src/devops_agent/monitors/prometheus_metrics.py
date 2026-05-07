from devops_agent.monitors.base import BaseMonitor
from devops_agent.event import AgentEvent

import logging
logger = logging.getLogger(__name__)

CPU_THRESHOLD = 1.0          # cores
LATENCY_THRESHOLD_S = 2.0    # seconds (p99)
ERROR_RATE_THRESHOLD = 0.05  # requests/s (5xx)

class PrometheusMetricsMonitor(BaseMonitor):
    def __init__(self, adapter, namespace: str = "iotag-dev") -> None:
        self._adapter = adapter
        self._namespace = namespace
        self._alerted: set[str] = set()

    def poll(self) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        events.extend(self._check_cpu())
        events.extend(self._check_http_latency())
        events.extend(self._check_error_rate())
        return events

    def _check_cpu(self) -> list[AgentEvent]:
        events = []
        query = (
            f'sum(rate(container_cpu_usage_seconds_total{{namespace="{self._namespace}",'
            f'container!=""}}[5m])) by (pod)'
        )
        result = self._adapter.run("prometheus", "query", {"query": query}, dry_run=False)
        for metric in result.get("results", []):
            pod = metric.get("metric", {}).get("pod", "")
            try:
                value = float(metric["value"][1])
            except (KeyError, ValueError, TypeError):
                continue
            key = f"high_cpu_usage:{pod}"
            if value > CPU_THRESHOLD and key not in self._alerted:
                self._alerted.add(key)
                events.append(AgentEvent(
                    kind="high_cpu_usage",
                    title=f"High CPU on pod {pod}: {value:.2f} cores",
                    context={
                        "pod": pod,
                        "cpu": value,
                        "namespace": self._namespace,
                        "threshold": CPU_THRESHOLD,
                    },
                ))
            elif value <= CPU_THRESHOLD:
                self._alerted.discard(key)
        return events

    def _check_http_latency(self) -> list[AgentEvent]:
        events = []
        query = (
            f'histogram_quantile(0.99, sum(rate('
            f'http_server_requests_seconds_bucket{{namespace="{self._namespace}"}}[5m]'
            f')) by (le, pod, uri))'
        )
        result = self._adapter.run("prometheus", "query", {"query": query}, dry_run=False)
        for metric in result.get("results", []):
            pod = metric.get("metric", {}).get("pod", "")
            uri = metric.get("metric", {}).get("uri", "")
            try:
                value = float(metric["value"][1])
            except (KeyError, ValueError, TypeError):
                continue
            key = f"high_http_latency:{pod}:{uri}"
            if value > LATENCY_THRESHOLD_S and key not in self._alerted:
                self._alerted.add(key)
                events.append(AgentEvent(
                    kind="high_http_latency",
                    title=f"High HTTP latency on {pod}{uri}: p99={value:.2f}s",
                    context={
                        "pod": pod,
                        "uri": uri,
                        "latency_p99_s": value,
                        "namespace": self._namespace,
                        "threshold_s": LATENCY_THRESHOLD_S,
                    },
                ))
            elif value <= LATENCY_THRESHOLD_S:
                self._alerted.discard(key)
        return events

    def _check_error_rate(self) -> list[AgentEvent]:
        events = []
        query = (
            f'sum(rate(http_server_requests_seconds_count{{namespace="{self._namespace}",'
            f'status=~"5.."}}[5m])) by (pod, uri)'
        )
        result = self._adapter.run("prometheus", "query", {"query": query}, dry_run=False)
        for metric in result.get("results", []):
            pod = metric.get("metric", {}).get("pod", "")
            uri = metric.get("metric", {}).get("uri", "")
            try:
                value = float(metric["value"][1])
            except (KeyError, ValueError, TypeError):
                continue
            key = f"high_error_rate:{pod}:{uri}"
            if value > ERROR_RATE_THRESHOLD and key not in self._alerted:
                self._alerted.add(key)
                events.append(AgentEvent(
                    kind="high_error_rate",
                    title=f"High 5xx error rate on {pod}{uri}: {value:.3f} req/s",
                    context={
                        "pod": pod,
                        "uri": uri,
                        "error_rate": value,
                        "namespace": self._namespace,
                        "threshold": ERROR_RATE_THRESHOLD,
                    },
                ))
            elif value <= ERROR_RATE_THRESHOLD:
                self._alerted.discard(key)
        return events
