from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx


def _normalize_base_url(value: str | None, default: str = "") -> str:
    if value is None:
        value = default
    value = str(value).strip()
    return value.rstrip("/") if value else ""


PROMETHEUS_URL_DEFAULT = _normalize_base_url(os.getenv("PROMETHEUS_URL"), "http://localhost:9090")
PROMETHEUS_USER        = os.getenv("PROMETHEUS_USER")
PROMETHEUS_PASSWORD    = os.getenv("PROMETHEUS_PASSWORD")
PROMETHEUS_TIMEOUT     = int(os.getenv("PROMETHEUS_TIMEOUT", "20"))

@dataclass
class PrometheusAdapter:
    """HTTP adapter for Prometheus HTTP API.

    Reads PROMETHEUS_URL from env (default http://localhost:9090).
    Set PROMETHEUS_USER / PROMETHEUS_PASSWORD for basic-auth (Grafana Cloud / Mimir).
    """

    base_url: str        = field(default_factory=lambda: _normalize_base_url(PROMETHEUS_URL_DEFAULT, "http://localhost:9090"))
    user: str | None     = field(default_factory=lambda: PROMETHEUS_USER)
    password: str | None = field(default_factory=lambda: PROMETHEUS_PASSWORD)
    timeout: int         = field(default_factory=lambda: PROMETHEUS_TIMEOUT)

    def query(self, query: str, time: str = "") -> dict[str, Any]:
        """Execute an instant PromQL query.

        Args:
            query – PromQL expression (required)
            time  – evaluation timestamp (optional, RFC3339 or unix)
        """
        if not query:
            return {"status": "error", "message": "query arg is required for Prometheus query"}
        return self._instant(query, time)

    def query_range(self, query: str, last_minutes: int = 30, step: str = "1m") -> dict[str, Any]:
        """Execute a PromQL range query.

        Args:
            query        – PromQL expression (required)
            last_minutes – look-back window in minutes (default 30)
            step         – resolution step, e.g. "1m" (default "1m")
        """
        if not query:
            return {"status": "error", "message": "query arg is required for Prometheus query_range"}
        return self._range(query, last_minutes, step)

    def run(self, action: str, args: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        if dry_run:
            return {
                "tool": "prometheus",
                "action": action,
                "args": args,
                "status": "simulated",
                "dry_run": True,
                "message": "PrometheusAdapter dry-run: no real query performed.",
            }

        if not self.base_url:
            return {
                "tool": "prometheus",
                "action": action,
                "args": args,
                "status": "error",
                "message": "PROMETHEUS_URL env variable not set.",
            }

        try:
            if action in ("query", "get_metrics"):
                result = self.query(**args)
            elif action == "query_range":
                result = self.query_range(**args)
            else:
                result = {"status": "error", "message": f"Unsupported Prometheus action: {action}"}
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}

        result.update({"tool": "prometheus", "action": action, "args": args, "dry_run": False})
        return result

    def _auth(self) -> tuple[str, str] | None:
        if self.user and self.password:
            return (self.user, self.password)
        return None

    def _instant(self, promql: str, time_str: str) -> dict[str, Any]:
        params: dict[str, str] = {"query": promql}
        if time_str:
            params["time"] = time_str

        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/api/v1/query", params=params, auth=self._auth())
            response.raise_for_status()
            data = response.json()

        results = data.get("data", {}).get("result", [])
        return {
            "status": "ok",
            "query": promql,
            "result_type": data.get("data", {}).get("resultType"),
            "result_count": len(results),
            "results": results,
        }

    def _range(self, promql: str, last_minutes: int, step: str) -> dict[str, Any]:
        import time

        end = int(time.time())
        start = end - last_minutes * 60

        params: dict[str, str] = {
            "query": promql,
            "start": str(start),
            "end": str(end),
            "step": step,
        }

        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(f"{self.base_url}/api/v1/query_range", params=params, auth=self._auth())
            response.raise_for_status()
            data = response.json()

        results = data.get("data", {}).get("result", [])
        return {
            "status": "ok",
            "query": promql,
            "result_type": data.get("data", {}).get("resultType"),
            "result_count": len(results),
            "results": results,
        }
