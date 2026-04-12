from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class LokiAdapter:
    """HTTP adapter for Grafana Loki LogQL API.

    Reads LOKI_URL from env (default http://localhost:3100).
    Set LOKI_USER / LOKI_PASSWORD for basic-auth (Grafana Cloud).
    """

    base_url: str = field(default_factory=lambda: os.getenv("LOKI_URL", "http://localhost:3100").rstrip("/"))
    user: str | None = field(default_factory=lambda: os.getenv("LOKI_USER"))
    password: str | None = field(default_factory=lambda: os.getenv("LOKI_PASSWORD"))
    timeout: int = 20

    # ------------------------------------------------------------------ #
    #  Public interface                                                    #
    # ------------------------------------------------------------------ #

    def query_range(self, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a LogQL range query.

        Expected args:
            query      – LogQL expression, e.g. '{namespace="iot-agent"}'
            service    – convenience: sets {app="<service>"} label filter
            namespace  – convenience: sets {namespace="<ns>"} label filter
            last_minutes – look-back window in minutes (default 30)
            limit      – max log lines returned (default 200)
        """
        logql = self._build_logql(args)
        last_minutes = int(args.get("last_minutes", 30))
        limit = int(args.get("limit", 200))
        return self._request(logql, last_minutes, limit)

    def run(self, action: str, args: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        if dry_run:
            return {
                "tool": "loki",
                "action": action,
                "args": args,
                "status": "simulated",
                "dry_run": True,
                "message": "LokiAdapter dry-run: no real query performed.",
            }

        if not self.base_url:
            return {
                "tool": "loki",
                "action": action,
                "args": args,
                "status": "error",
                "message": "LOKI_URL env variable not set.",
            }

        try:
            if action in ("query_range", "get_logs"):
                result = self.query_range(args)
            else:
                result = {"status": "error", "message": f"Unsupported Loki action: {action}"}
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}

        result.update({"tool": "loki", "action": action, "args": args, "dry_run": False})
        return result

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    def _build_logql(self, args: dict[str, Any]) -> str:
        if args.get("query"):
            return str(args["query"])

        labels: dict[str, str] = {}
        if args.get("namespace"):
            labels["namespace"] = str(args["namespace"])
        if args.get("service"):
            labels["app"] = str(args["service"])

        if not labels:
            labels["namespace"] = "iot-agent"

        selector = ", ".join(f'{k}="{v}"' for k, v in labels.items())
        return "{" + selector + "}"

    def _request(self, logql: str, last_minutes: int, limit: int) -> dict[str, Any]:
        import time

        end_ns = int(time.time() * 1e9)
        start_ns = end_ns - last_minutes * 60 * int(1e9)

        params = {
            "query": logql,
            "start": str(start_ns),
            "end": str(end_ns),
            "limit": str(limit),
            "direction": "backward",
        }

        auth = (self.user, self.password) if self.user and self.password else None
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}/loki/api/v1/query_range",
                params=params,
                auth=auth,
            )
            response.raise_for_status()
            data = response.json()

        streams = data.get("data", {}).get("result", [])
        lines: list[str] = []
        for stream in streams:
            for _ts, line in stream.get("values", []):
                lines.append(line)

        return {
            "status": "ok",
            "query": logql,
            "line_count": len(lines),
            "lines": lines[:limit],
        }
