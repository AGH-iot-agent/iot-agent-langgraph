
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, TypedDict
from devops_agent.event import AgentEvent

logger = logging.getLogger(__name__)

@dataclass
class WatchdogResponse:
    status: str
    message: str
    state: dict[str, Any] | None = None

@dataclass
class WatchdogConfig:
    namespace:        str  = "iot-agent"
    interval_seconds: int  = 30
    monitor_github:   bool = True
    github_org:       str  = "AGH-iot-agent"
    issue_max_age_s:  int  = 86400

@dataclass
class Alert:
    ts:      int
    kind:    str
    title:   str
    details: dict[str, Any]
    
class AgentState(TypedDict, total=False):
    event_kind: str
    issue_title: str
    issue_body: str

class StatusReport(TypedDict):
    running: bool
    last_tick: int | None
    last_error: str | None
    config: dict[str, Any] | None

class AgentWatchdog:
    def __init__(self, adapter, graph, monitors) -> None:
        self._adapter = adapter
        self._graph = graph
        self._monitors = monitors
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock   = threading.Lock()
        self._alerts: list[Alert] = []
        self._status: StatusReport = {
            "running": False, "last_tick": None,
            "last_error": None, "config": None,
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

            # Clear initial status 
            self._status = StatusReport(
                running    = True, 
                last_tick  = None, 
                last_error = None,
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

    def _append_alert(self, kind: str, title: str, details: dict[str, Any]) -> None:
        with self._lock:
            self._alerts.append(Alert(ts=int(time.time()), kind=kind, title=title, details=details))
            if len(self._alerts) > 500:
                self._alerts = self._alerts[-500:]

    def _loop(self, config: WatchdogConfig) -> None:
        while not self._stop_event.is_set():
            tick_error = None
            try:
                self._tick(config)
            except Exception as exc:
                tick_error = str(exc)
                logger.exception("[WATCHDOG] tick failed")
            finally:
                with self._lock:
                    self._status["last_tick"]  = int(time.time())
                    self._status["last_error"] = tick_error
            self._stop_event.wait(timeout=max(5, config.interval_seconds))

    def _tick(self, config: WatchdogConfig) -> None:
        for monitor in self._monitors:
            try:
                events = monitor.poll()
            except Exception:
                logger.exception("[WATCHDOG] Monitor %s failed", type(monitor).__name__)
                continue
            for event in events:
                threading.Thread(target=self._dispatch, args=(event,), daemon=True).start()

    def _dispatch(self, event: AgentEvent) -> None:
        try:
            state = {
                "request":        event.to_prompt(),
                "event_kind":     event.kind,              
                "issue_title":    event.title,           
                "issue_body":     event.body,             
                "dry_run":        False,
                "context":        event.context,
                "max_revisions":  1,
                "revision_count": 0,
            }
            result = self._graph.invoke(state)
            comment = result.get("final_summary") or result.get("execution_summary") or event.to_prompt()

            if event.kind == "github_issue":
                repo_full_name = event.context.get("repo_full_name")
                issue_number = event.context.get("issue_number")
                if repo_full_name and issue_number:
                    self._adapter.run(
                        "github", "create_issue_comment",
                        {"repo": repo_full_name, "issue_number": issue_number, "body": comment},
                        dry_run=False,
                    )

            self._append_alert(event.kind, event.title, event.context)

        except Exception:
            logger.exception("[WATCHDOG] Dispatch failed for event: %s", event.title)
