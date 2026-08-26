from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from statistics import mean
from typing import Any

from prometheus_client import Counter, Histogram

# ---------------------------------------------------------------------------
# Prometheus metrics – LangGraph agent telemetry
# ---------------------------------------------------------------------------

_LABEL_EVENT_KIND = ["event_kind"]

PROM_RUNS_TOTAL = Counter(
    "iotag_runs_total",
    "Total number of agent graph runs",
    _LABEL_EVENT_KIND,
)
PROM_MTTR_SECONDS = Histogram(
    "iotag_mttr_seconds",
    "Mean Time To Remediate – seconds from event detection to graph completion",
    _LABEL_EVENT_KIND,
    buckets=[5, 10, 20, 30, 60, 90, 120, 180, 300, 600],
)
PROM_INPUT_TOKENS_TOTAL = Counter(
    "iotag_input_tokens_total",
    "Total LLM input tokens consumed across all runs",
    _LABEL_EVENT_KIND,
)
PROM_OUTPUT_TOKENS_TOTAL = Counter(
    "iotag_output_tokens_total",
    "Total LLM output tokens produced across all runs",
    _LABEL_EVENT_KIND,
)
PROM_TOTAL_TOKENS_TOTAL = Counter(
    "iotag_total_tokens_total",
    "Total LLM tokens (input + output) across all runs",
    _LABEL_EVENT_KIND,
)
PROM_PLAN_STEPS = Histogram(
    "iotag_plan_steps",
    "Number of plan steps generated per run",
    _LABEL_EVENT_KIND,
    buckets=[1, 2, 3, 5, 7, 10, 15, 20],
)
PROM_TOOL_CALL_ROUNDS = Histogram(
    "iotag_tool_call_rounds",
    "Number of tool-call rounds per run",
    _LABEL_EVENT_KIND,
    buckets=[1, 2, 3, 4, 5, 6, 8, 10, 15],
)
PROM_REVISION_COUNT = Histogram(
    "iotag_revision_count",
    "Number of plan revisions per run",
    _LABEL_EVENT_KIND,
    buckets=[0, 1, 2, 3, 5],
)
PROM_VALIDATION_TOTAL = Counter(
    "iotag_validation_total",
    "Sandbox validation outcomes",
    ["event_kind", "result"],           # result: passed | failed
)
PROM_PR_CREATED_TOTAL = Counter(
    "iotag_pr_created_total",
    "Total number of pull-requests opened by the agent",
    _LABEL_EVENT_KIND,
)
PROM_SLO_VIOLATIONS_TOTAL = Counter(
    "iotag_slo_violations_total",
    "SLO violations detected during runs",
    ["event_kind", "metric"],           # metric: mttr_s | input_tokens | tool_call_rounds
)
PROM_SECURITY_VIOLATIONS_TOTAL = Counter(
    "iotag_security_violations_total",
    "Security violations detected during runs (threat-type label)",
    ["threat_type"],
)

_SLO: dict[str, dict[str, float | int]] = {
    "github_issue": {"mttr_s": 90,  "input_tokens": 8000,  "tool_call_rounds": 6},
    "github_ci_failure":    {"mttr_s": 120, "input_tokens": 12000, "tool_call_rounds": 8},
    "github_pr_build_failure": {"mttr_s": 120, "input_tokens": 12000, "tool_call_rounds": 8},
    "github_pr":            {"mttr_s": 120, "input_tokens": 12000, "tool_call_rounds": 8},
    "prometheus_alert":     {"mttr_s": 60,  "input_tokens": 10000, "tool_call_rounds": 7},
    "prometheus_metrics":   {"mttr_s": 60,  "input_tokens": 10000, "tool_call_rounds": 7},
    "loki_error":           {"mttr_s": 60,  "input_tokens": 10000, "tool_call_rounds": 7},
}
_SLO_DEFAULT: dict[str, float | int] = {"mttr_s": 120, "input_tokens": 12000, "tool_call_rounds": 8}


def _slo_violations(event: "EventMetrics") -> list[str]:
    thresholds = _SLO.get(event.event_kind, _SLO_DEFAULT)
    violations: list[str] = []
    if event.mttr_s > thresholds["mttr_s"]:
        violations.append(f"mttr_s {event.mttr_s:.1f} > {thresholds['mttr_s']}")
    if event.input_tokens > thresholds["input_tokens"]:
        violations.append(f"input_tokens {event.input_tokens} > {thresholds['input_tokens']}")
    if event.tool_call_rounds > thresholds["tool_call_rounds"]:
        violations.append(f"tool_call_rounds {event.tool_call_rounds} > {thresholds['tool_call_rounds']}")
    return violations


@dataclass
class ManualBaseline:
    scenario_id: str
    name: str
    category: str
    manual_mttr_s: float
    manual_steps_count: int
    manual_resolver_role: str
    notes: str


def load_manual_baselines(path: Path | None = None) -> dict[str, ManualBaseline]:
    """Wczytuje plik manual_baselines.json i zwraca słownik keyed by scenario_id."""
    if path is None:
        path = Path(__file__).parent.parent.parent.parent / "data" / "manual_baselines.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    result: dict[str, ManualBaseline] = {}
    for b in data.get("baselines", []):
        sid = str(b["scenario_id"]).zfill(2)
        result[sid] = ManualBaseline(
            scenario_id=sid,
            name=b.get("name", ""),
            category=b.get("category", ""),
            manual_mttr_s=float(b.get("manual_mttr_s", 0)),
            manual_steps_count=int(b.get("manual_steps_count", 0)),
            manual_resolver_role=b.get("manual_resolver_role", ""),
            notes=b.get("notes", ""),
        )
    return result


@dataclass
class EventMetrics:
    trace_id: str
    event_kind: str
    repo: str
    title: str
    mttr_s: float
    plan_step_count: int
    tool_call_rounds: int
    revision_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    validation_passed: bool
    pr_created: bool


@dataclass
class AdapterCallMetrics:
    service: str
    tool: str
    status: str
    timeout: bool
    latency_ms: float


class AgentMetrics:
    def __init__(self, max_events: int = 1000) -> None:
        self._max_events = max_events
        self._events: list[EventMetrics] = []
        self._adapter_calls: list[AdapterCallMetrics] = []
        self._lock = threading.Lock()

    def record(self, payload: dict[str, Any]) -> None:
        event = EventMetrics(
            trace_id=str(payload.get("trace_id", "")),
            event_kind=str(payload.get("event_kind", "")),
            repo=str(payload.get("repo", "")),
            title=str(payload.get("title", "")),
            mttr_s=float(payload.get("mttr_s", 0.0)),
            plan_step_count=int(payload.get("plan_step_count", 0)),
            tool_call_rounds=int(payload.get("tool_call_rounds", 0)),
            revision_count=int(payload.get("revision_count", 0)),
            input_tokens=int(payload.get("input_tokens", 0)),
            output_tokens=int(payload.get("output_tokens", 0)),
            total_tokens=int(payload.get("total_tokens", 0)),
            validation_passed=bool(payload.get("validation_passed", False)),
            pr_created=bool(payload.get("pr_created", False)),
        )

        # -- Prometheus counters / histograms --------------------------------
        kind = event.event_kind or "unknown"
        PROM_RUNS_TOTAL.labels(event_kind=kind).inc()
        PROM_MTTR_SECONDS.labels(event_kind=kind).observe(event.mttr_s)
        PROM_INPUT_TOKENS_TOTAL.labels(event_kind=kind).inc(event.input_tokens)
        PROM_OUTPUT_TOKENS_TOTAL.labels(event_kind=kind).inc(event.output_tokens)
        PROM_TOTAL_TOKENS_TOTAL.labels(event_kind=kind).inc(event.total_tokens)
        PROM_PLAN_STEPS.labels(event_kind=kind).observe(event.plan_step_count)
        PROM_TOOL_CALL_ROUNDS.labels(event_kind=kind).observe(event.tool_call_rounds)
        PROM_REVISION_COUNT.labels(event_kind=kind).observe(event.revision_count)
        PROM_VALIDATION_TOTAL.labels(
            event_kind=kind,
            result="passed" if event.validation_passed else "failed",
        ).inc()
        if event.pr_created:
            PROM_PR_CREATED_TOTAL.labels(event_kind=kind).inc()
        for violation in _slo_violations(event):
            metric_name = violation.split(" ")[0]
            PROM_SLO_VIOLATIONS_TOTAL.labels(event_kind=kind, metric=metric_name).inc()
        # --------------------------------------------------------------------

        with self._lock:
            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events :]

    def record_adapter_call(self, payload: dict[str, Any]) -> None:
        call = AdapterCallMetrics(
            service=str(payload.get("service", "")),
            tool=str(payload.get("tool", "")),
            status=str(payload.get("status", "unknown")),
            timeout=bool(payload.get("timeout", False)),
            latency_ms=float(payload.get("latency_ms", 0.0)),
        )
        with self._lock:
            self._adapter_calls.append(call)
            if len(self._adapter_calls) > self._max_events:
                self._adapter_calls = self._adapter_calls[-self._max_events :]

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            selected = self._events[-max(1, limit) :]
            return [asdict(e) for e in selected]

    def adapter_summary(self) -> dict[str, Any]:
        with self._lock:
            calls = list(self._adapter_calls)

        if not calls:
            return {
                "count": 0,
                "timeout_rate": 0.0,
                "degraded_rate": 0.0,
                "error_rate": 0.0,
                "avg_latency_ms": 0.0,
                "by_service": {},
            }

        by_service: dict[str, dict[str, Any]] = {}
        for c in calls:
            bucket = by_service.setdefault(
                c.service,
                {"count": 0, "timeout_count": 0, "degraded_count": 0, "error_count": 0, "avg_latency_ms": 0.0},
            )
            bucket["count"] += 1
            if c.timeout:
                bucket["timeout_count"] += 1
            if c.status == "degraded":
                bucket["degraded_count"] += 1
            if c.status == "error":
                bucket["error_count"] += 1

        for service in by_service:
            subset = [c for c in calls if c.service == service]
            by_service[service]["avg_latency_ms"] = round(mean(c.latency_ms for c in subset), 2)

        count = len(calls)
        timeout_count = sum(1 for c in calls if c.timeout)
        degraded_count = sum(1 for c in calls if c.status == "degraded")
        error_count = sum(1 for c in calls if c.status == "error")

        return {
            "count": count,
            "timeout_rate": round(timeout_count / count, 3),
            "degraded_rate": round(degraded_count / count, 3),
            "error_rate": round(error_count / count, 3),
            "avg_latency_ms": round(mean(c.latency_ms for c in calls), 2),
            "by_service": by_service,
        }

    def summary(self) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)

        if not events:
            return {
                "count": 0,
                "avg_mttr_s": 0.0,
                "avg_input_tokens": 0.0,
                "avg_output_tokens": 0.0,
                "avg_total_tokens": 0.0,
                "avg_plan_steps": 0.0,
                "avg_tool_rounds": 0.0,
                "pr_created_rate": 0.0,
                "validation_pass_rate": 0.0,
                "by_event_kind": {},
            }

        by_kind: dict[str, dict[str, Any]] = {}
        for e in events:
            bucket = by_kind.setdefault(e.event_kind, {"count": 0, "avg_mttr_s": 0.0, "avg_total_tokens": 0.0})
            bucket["count"] += 1

        for kind in by_kind:
            subset = [e for e in events if e.event_kind == kind]
            by_kind[kind]["avg_mttr_s"] = round(mean(e.mttr_s for e in subset), 3)
            by_kind[kind]["avg_total_tokens"] = round(mean(e.total_tokens for e in subset), 2)

        # SLO violations per event
        slo_violations: list[dict[str, Any]] = []
        for e in events:
            viols = _slo_violations(e)
            if viols:
                slo_violations.append({
                    "trace_id": e.trace_id,
                    "event_kind": e.event_kind,
                    "title": e.title,
                    "violations": viols,
                })

        return {
            "count": len(events),
            "avg_mttr_s": round(mean(e.mttr_s for e in events), 3),
            "avg_input_tokens": round(mean(e.input_tokens for e in events), 2),
            "avg_output_tokens": round(mean(e.output_tokens for e in events), 2),
            "avg_total_tokens": round(mean(e.total_tokens for e in events), 2),
            "avg_plan_steps": round(mean(e.plan_step_count for e in events), 2),
            "avg_tool_rounds": round(mean(e.tool_call_rounds for e in events), 2),
            "pr_created_rate": round(sum(1 for e in events if e.pr_created) / len(events), 3),
            "validation_pass_rate": round(sum(1 for e in events if e.validation_passed) / len(events), 3),
            "slo_violations": slo_violations,
            "by_event_kind": by_kind,
        }


agent_metrics = AgentMetrics()


def record_security_violation(threat_type: str) -> None:
    """Inkrementuje licznik Prometheus dla naruszeń bezpieczeństwa."""
    PROM_SECURITY_VIOLATIONS_TOTAL.labels(threat_type=threat_type or "unknown").inc()
