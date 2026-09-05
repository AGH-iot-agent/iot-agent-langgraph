
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, TypedDict
from uuid import uuid4
from devops_agent.event import AgentEvent
from devops_agent.metrics import agent_metrics

logger = logging.getLogger(__name__)


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid int env %s=%r; using default=%s", name, raw, default)
        return default

WATCHDOG_DEFAULT_NAMESPACE                = os.environ.get("WATCHDOG_DEFAULT_NAMESPACE", "iot-agent")
WATCHDOG_DEFAULT_INTERVAL_SECONDS         = _env_int("WATCHDOG_DEFAULT_INTERVAL_SECONDS", 30)
WATCHDOG_DEFAULT_MONITOR_GITHUB           = _is_truthy(os.environ.get("WATCHDOG_DEFAULT_MONITOR_GITHUB"))
WATCHDOG_DEFAULT_GITHUB_ORG               = os.environ.get("WATCHDOG_DEFAULT_GITHUB_ORG", "")
WATCHDOG_DEFAULT_ISSUE_MAX_AGE_S          = _env_int("WATCHDOG_DEFAULT_ISSUE_MAX_AGE_S", 3600)
WATCHDOG_DEFAULT_MONITOR_GITHUB_ISSUES    = _is_truthy(os.environ.get("WATCHDOG_DEFAULT_MONITOR_GITHUB_ISSUES"))
WATCHDOG_DEFAULT_MONITOR_GITHUB_PRS       = _is_truthy(os.environ.get("WATCHDOG_DEFAULT_MONITOR_GITHUB_PRS"))
WATCHDOG_DEFAULT_MONITOR_GITHUB_CI        = _is_truthy(os.environ.get("WATCHDOG_DEFAULT_MONITOR_GITHUB_CI"))
WATCHDOG_DEFAULT_MONITOR_GITHUB_PR_BUILDS = _is_truthy(os.environ.get("WATCHDOG_DEFAULT_MONITOR_GITHUB_PR_BUILDS"))
WATCHDOG_DEFAULT_MONITOR_PROMETHEUS       = _is_truthy(os.environ.get("WATCHDOG_DEFAULT_MONITOR_PROMETHEUS"))
WATCHDOG_DEFAULT_MONITOR_LOKI             = _is_truthy(os.environ.get("WATCHDOG_DEFAULT_MONITOR_LOKI"))
WATCHDOG_DEFAULT_MONITOR_K8S              = _is_truthy(os.environ.get("WATCHDOG_DEFAULT_MONITOR_K8S"))

@dataclass
class WatchdogResponse:
    status: str
    message: str
    state: dict[str, Any] | None = None

@dataclass
class WatchdogConfig:
    namespace:                str  = WATCHDOG_DEFAULT_NAMESPACE 
    interval_seconds:         int  = WATCHDOG_DEFAULT_INTERVAL_SECONDS
    monitor_github:           bool = WATCHDOG_DEFAULT_MONITOR_GITHUB
    github_org:               str  = WATCHDOG_DEFAULT_GITHUB_ORG
    issue_max_age_s:          int  = WATCHDOG_DEFAULT_ISSUE_MAX_AGE_S
    monitor_github_issues:    bool = WATCHDOG_DEFAULT_MONITOR_GITHUB_ISSUES
    monitor_github_prs:       bool = WATCHDOG_DEFAULT_MONITOR_GITHUB_PRS
    monitor_github_ci:        bool = WATCHDOG_DEFAULT_MONITOR_GITHUB_CI
    monitor_github_pr_builds: bool = WATCHDOG_DEFAULT_MONITOR_GITHUB_PR_BUILDS
    monitor_prometheus:       bool = WATCHDOG_DEFAULT_MONITOR_PROMETHEUS
    monitor_loki:             bool = WATCHDOG_DEFAULT_MONITOR_LOKI
    monitor_k8s:              bool = WATCHDOG_DEFAULT_MONITOR_K8S

@dataclass
class Alert:
    ts:      int
    kind:    str
    title:   str
    details: dict[str, Any]
    
class AgentState(TypedDict, total=False):
    event_kind:  str
    issue_title: str
    issue_body:  str

class StatusReport(TypedDict):
    running:    bool
    last_tick:  int | None
    last_error: str | None
    consecutive_failures: int
    config:     dict[str, Any] | None

_DISPATCH_SEMAPHORE = threading.Semaphore(3)


class AgentWatchdog:
    def __init__(self, adapter, graph, monitors) -> None:
        self._adapter = adapter
        self._graph = graph
        self._monitors = monitors
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock   = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="watchdog-dispatch")
        self._alerts: list[Alert] = []
        self._last_graph_state: dict[str, Any] | None = None
        self._status: StatusReport = {
            "running": False, "last_tick": None,
            "last_error": None, "consecutive_failures": 0, "config": None,
        }

    def start(self, config: WatchdogConfig) -> WatchdogResponse:
        logger.info("[WATCHDOG] start: %s", config)
        with self._lock:
            if self._thread and self._thread.is_alive():
                return WatchdogResponse(
                    status  = "ok", 
                    message = "already running", 
                    state   = self.status())
            
            self._stop_event.clear()

            initial_config = { 
                "namespace":        config.namespace,
                "interval_seconds": config.interval_seconds,
                "github_org":       config.github_org,
            }

            self._status = StatusReport(
                running    = True, 
                last_tick  = None, 
                last_error = None,
                consecutive_failures = 0,
                config     = initial_config,
            )
            
            self._thread = threading.Thread(target=self._loop, args=(config,), daemon=True)
            self._thread.start()

        return WatchdogResponse(
            status  = "ok", 
            message = "watchdog started", 
            state   = self.status()
        )

    def stop(self) -> WatchdogResponse:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        with self._lock:
            self._status["running"] = False

        return WatchdogResponse(
            status  = "ok", 
            message = "watchdog stopped", 
            state   = self.status()
        )

    def status(self) -> WatchdogResponse:
        with self._lock:
            return WatchdogResponse(
                status  = "ok",
                message = "status retrieved",
                state   = dict(self._status)
            )

    def recent_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"ts": a.ts, "kind": a.kind, "title": a.title, "details": a.details}
                for a in self._alerts[-max(1, limit):]
            ]

    def last_graph_state(self) -> dict[str, Any]:
        """Slim telemetry from the most recent graph.invoke (plan, intercepts, security)."""
        with self._lock:
            return dict(self._last_graph_state or {})

    def _store_graph_state(self, result: dict[str, Any]) -> None:
        validation = result.get("validation_result") or {}
        slim = {
            "security_blocked": bool(result.get("security_blocked")),
            "guardrails_enabled": result.get("guardrails_enabled"),
            "requested_forbidden_actions": list(result.get("requested_forbidden_actions") or []),
            "forbidden_tools_requested": list(result.get("forbidden_tools_requested") or []),
            "validation_passed": bool(validation.get("passed")),
            "fix_attempt": int(result.get("fix_attempt") or 0),
            "pr_created": bool(result.get("pr_url")),
            "pr_url": str(result.get("pr_url") or ""),
            "validation_errors": list(validation.get("errors") or []),
            "plan": list(result.get("plan") or []),
            "execution_results": [
                {
                    "action": step.get("action"),
                    "tool": step.get("tool"),
                    "result": {
                        "status": (step.get("result") or {}).get("status"),
                        "executed": (step.get("result") or {}).get("executed"),
                        "unsafe_action": (step.get("result") or {}).get("unsafe_action"),
                    },
                }
                for step in (result.get("execution_results") or [])
                if isinstance(step, dict)
            ],
        }
        with self._lock:
            self._last_graph_state = slim

    def _append_alert(self, kind: str, title: str, details: dict[str, Any]) -> None:
        with self._lock:
            self._alerts.append(Alert(ts=int(time.time()), kind=kind, title=title, details=details))
            if len(self._alerts) > 500:
                self._alerts = self._alerts[-500:]

    def _loop(self, config: WatchdogConfig) -> None:
        while not self._stop_event.is_set():
            tick_error = None
            try:
                self._tick()
            except Exception as exc:
                tick_error = str(exc)
                logger.exception("[WATCHDOG] tick failed")
            finally:
                with self._lock:
                    self._status["last_tick"]  = int(time.time())
                    self._status["last_error"] = tick_error
                    if tick_error:
                        self._status["consecutive_failures"] = int(self._status.get("consecutive_failures", 0)) + 1
                    else:
                        self._status["consecutive_failures"] = 0
            self._stop_event.wait(timeout=max(5, config.interval_seconds))

    def _tick(self) -> None:
        for monitor in self._monitors:
            try:
                events = monitor.poll()
            except Exception:
                logger.exception("[WATCHDOG] Monitor %s failed", type(monitor).__name__)
                continue
            for event in events:
                future = self._executor.submit(self._dispatch, event)
                future.add_done_callback(self._log_future_error)

    @staticmethod
    def _log_future_error(future) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("[WATCHDOG] Dispatch future failed")

    def _dispatch(self, event: AgentEvent) -> None:
        with _DISPATCH_SEMAPHORE:
            self._dispatch_inner(event)

    def _dispatch_inner(self, event: AgentEvent) -> None:
        started_at = time.time()
        trace_id = str(event.context.get("trace_id") or f"watchdog-{uuid4()}")
        event_context = dict(event.context)
        event_context["trace_id"] = trace_id
        configured_namespace = os.environ.get("KUBERNETES_NAMESPACE", "iotag-dev")
        autoapply_enabled = _is_truthy(os.environ.get("AGENT_AUTOAPPLY"))

        effective_dry_run = False
        logger.info(
            "[WATCHDOG] Dispatch start event_kind=%s title=%s trace_id=%s dry_run=%s autoapply=%s namespace=%s",
            event.kind,
            event.title,
            trace_id,
            effective_dry_run,
            autoapply_enabled,
            configured_namespace,
        )

        try:
            issue_payload = event_context.get("issue") if isinstance(event_context.get("issue"), dict) else {}
            issue_body = event.body or str(issue_payload.get("body") or "")
            state = {
                "trace_id":       trace_id,
                "request":        f"[{event.kind.upper()}] {event.title}\n\n{issue_body}",
                "event_kind":     event.kind,
                "issue_title":    event.title,
                "issue_body":     issue_body,
                "dry_run":        effective_dry_run,
                "context":        event_context,
                "max_revisions":  1,
                "revision_count": 0,
                "started_at":     time.time(),
            }
            result = self._graph.invoke(state)
            if isinstance(result, dict):
                self._store_graph_state(result)
            raw_comment = result.get("final_summary") or result.get("execution_summary") or event.to_prompt()
            comment = f"## gh_action_bot\n\n{raw_comment}"

            if event.kind == "github_issue":
                repo_full_name = event.context.get("repo_full_name")
                issue_number = event.context.get("issue_number")
                if repo_full_name and issue_number:
                    pr_url = result.get("pr_url")
                    if pr_url:
                        comment = f"{comment}\n\n> 🤖 Agent created a fix PR: {pr_url}"
                    self._adapter.run(
                        "github", "create_issue_comment",
                        {"repo": repo_full_name, "issue_number": issue_number, "body": comment},
                        dry_run=False,
                    )

            elif event.kind == "github_pr":
                repo_full_name = event.context.get("repo_full_name")
                pr_number = event.context.get("pr_number")
                if repo_full_name and pr_number:
                    self._adapter.run(
                        "github", "create_issue_comment",
                        {"repo": repo_full_name, "issue_number": pr_number, "body": comment},
                        dry_run=False,
                    )

            elif event.kind in ("github_ci_failure", "github_pr_build_failure"):
                pr_url = result.get("pr_url")
                final = result.get("final_summary", "")
                if pr_url:
                    target = result.get("fix_target_branch", "main")
                    pr_num = result.get("fix_pr_number")
                    if event.kind == "github_pr_build_failure" and pr_num:
                        logger.info(
                            "[WATCHDOG] Fix PR created for PR#%d → branch '%s': %s",
                            pr_num, target, pr_url,
                        )
                    else:
                        logger.info("[WATCHDOG] Fix PR created for %s: %s", event.kind, pr_url)
                else:
                    validation = result.get("validation_result") or {}
                    if not validation.get("passed"):
                        logger.info(
                            "[WATCHDOG] No PR for %s (%s) — blocked by failed validation",
                            event.kind, event.title,
                        )
                    else:
                        logger.warning(
                            "[WATCHDOG] No PR created for %s (%s): %s",
                            event.kind, event.title, final[:300],
                        )

            self._append_alert(event.kind, event.title, event.context)
            logger.info(
                "[WATCHDOG] Dispatch end event_kind=%s title=%s trace_id=%s mttr_s=%.3f",
                event.kind,
                event.title,
                trace_id,
                time.time() - started_at,
            )

        except Exception:
            logger.exception(
                "[WATCHDOG] Dispatch failed event_kind=%s title=%s trace_id=%s",
                event.kind,
                event.title,
                trace_id,
            )
