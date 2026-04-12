from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from devops_agent.mcp_adapter import MCPAdapter


@dataclass
class WatchdogConfig:
    namespace: str = "iot-agent"
    interval_seconds: int = 30
    monitor_github: bool = True


class AgentWatchdog:
    def __init__(self, adapter: MCPAdapter) -> None:
        self._adapter = adapter
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._alerts: list[dict[str, Any]] = []
        self._status: dict[str, Any] = {
            "running": False,
            "last_tick": None,
            "last_error": None,
            "config": None,
        }

    def start(self, config: WatchdogConfig) -> dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"status": "ok", "message": "watchdog already running", "state": self.status()}

            self._stop_event.clear()
            self._status = {
                "running": True,
                "last_tick": None,
                "last_error": None,
                "config": {
                    "namespace": config.namespace,
                    "interval_seconds": config.interval_seconds,
                    "monitor_github": config.monitor_github,
                },
            }
            self._thread = threading.Thread(target=self._loop, args=(config,), daemon=True)
            self._thread.start()
        return {"status": "ok", "message": "watchdog started", "state": self.status()}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._stop_event.set()
            th = self._thread
            self._thread = None
            self._status["running"] = False
        if th and th.is_alive():
            th.join(timeout=3)
        return {"status": "ok", "message": "watchdog stopped", "state": self.status()}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "last_tick": self._status.get("last_tick"),
                "last_error": self._status.get("last_error"),
                "config": self._status.get("config"),
                "alerts_count": len(self._alerts),
            }

    def recent_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return self._alerts[-max(1, limit) :]

    def _append_alert(self, kind: str, title: str, details: dict[str, Any]) -> None:
        alert = {
            "ts": int(time.time()),
            "kind": kind,
            "title": title,
            "details": details,
        }
        with self._lock:
            self._alerts.append(alert)
            if len(self._alerts) > 500:
                self._alerts = self._alerts[-500:]

    def _loop(self, config: WatchdogConfig) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick(config)
                with self._lock:
                    self._status["last_tick"] = int(time.time())
                    self._status["last_error"] = None
            except Exception as exc:
                with self._lock:
                    self._status["last_error"] = str(exc)

            self._stop_event.wait(timeout=max(5, config.interval_seconds))

    def _tick(self, config: WatchdogConfig) -> None:
        ns = config.namespace

        rollout = self._adapter.run("kubernetes", "get_rollout_status", {"namespace": ns}, dry_run=False)
        if rollout.get("status") == "ok":
            for dep in rollout.get("deployments", []) or []:
                desired = int(dep.get("desired", 0))
                ready = int(dep.get("ready", 0))
                if desired > ready:
                    self._append_alert(
                        "k8s_rollout",
                        "Deployment not fully ready",
                        {
                            "namespace": ns,
                            "deployment": dep.get("name"),
                            "desired": desired,
                            "ready": ready,
                            "available": dep.get("available", 0),
                        },
                    )

        pods = self._adapter.run("kubernetes", "get_pods", {"namespace": ns}, dry_run=False)
        if pods.get("status") == "ok":
            for pod in pods.get("pods", []) or []:
                phase = str(pod.get("phase", ""))
                restarts = int(pod.get("restarts", 0))
                ready = bool(pod.get("ready", False))
                if phase not in {"Running", "Succeeded"} or not ready or restarts > 3:
                    self._append_alert(
                        "k8s_pod",
                        "Pod unhealthy",
                        {
                            "namespace": ns,
                            "pod": pod.get("name"),
                            "phase": phase,
                            "ready": ready,
                            "restarts": restarts,
                            "node": pod.get("node"),
                        },
                    )

        events = self._adapter.run("kubernetes", "get_events", {"namespace": ns}, dry_run=False)
        if events.get("status") == "ok":
            for event in events.get("events", []) or []:
                if str(event.get("type", "")).lower() == "warning":
                    self._append_alert(
                        "k8s_event",
                        "Kubernetes warning event",
                        {
                            "namespace": ns,
                            "reason": event.get("reason"),
                            "message": event.get("message"),
                            "object": event.get("object"),
                            "last_time": event.get("last_time"),
                        },
                    )

        if config.monitor_github:
            runs = self._adapter.run(
                "github",
                "list_org_workflow_runs",
                {"per_page": 5, "status": "failure"},
                dry_run=False,
            )
            if runs.get("status") == "ok":
                for run in runs.get("workflow_runs", []) or []:
                    if str(run.get("conclusion", "")).lower() == "failure":
                        self._append_alert(
                            "github_actions",
                            "Workflow failure detected",
                            {
                                "repo": run.get("repository", {}).get("full_name"),
                                "workflow": run.get("name"),
                                "run_id": run.get("id"),
                                "url": run.get("html_url"),
                            },
                        )