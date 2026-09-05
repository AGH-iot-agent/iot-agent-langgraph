from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import httpcore

logger = logging.getLogger(__name__)


def _normalize_base_url(value: str | None, default: str = "") -> str:
    if value is None:
        value = default
    value = str(value).strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"http://{value.lstrip('/')}"
    return value.rstrip("/")


LOKI_URL_DEFAULT     = _normalize_base_url(os.getenv("LOKI_URL"))
LOKI_USER            = os.getenv("LOKI_USER")
LOKI_PASSWORD        = os.getenv("LOKI_PASSWORD")
LOKI_TIMEOUT_CONNECT = int(os.getenv("LOKI_TIMEOUT_CONNECT", "5"))
LOKI_TIMEOUT_READ    = int(os.getenv("LOKI_TIMEOUT_READ", "20"))
LOKI_QUERY_RETRY_MAX = max(1, int(os.getenv("LOKI_QUERY_RETRY_MAX", "2")))

@dataclass
class LokiAdapter:
    base_url: str           = field(default_factory=lambda: _normalize_base_url(LOKI_URL_DEFAULT))
    user: str | None        = field(default_factory=lambda: LOKI_USER)
    password: str | None    = field(default_factory=lambda: LOKI_PASSWORD)
    timeout_connect: int    = field(default_factory=lambda: LOKI_TIMEOUT_CONNECT)
    timeout_read: int       = field(default_factory=lambda: LOKI_TIMEOUT_READ)
    retry_max_attempts: int = field(default_factory=lambda: LOKI_QUERY_RETRY_MAX)

    def query_range(self, **kwargs) -> dict[str, Any]:
        """Execute a LogQL range query.

        Keyword args:
            query        – LogQL expression, e.g. '{namespace="iot-agent"}'
            service      – convenience: sets {app="<service>"} label filter
            namespace    – convenience: sets {namespace="<ns>"} label filter
            last_minutes – look-back window in minutes (default 30)
            limit        – max log lines returned (default 200)
        """
        if not self.base_url:
            return {
                "status": "error",
                "message": "LOKI_URL env variable not set.",
            }

        logql = self._build_logql(kwargs)
        last_minutes = int(kwargs.get("last_minutes", 30))
        limit = int(kwargs.get("limit", 200))
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
                result = self.query_range(**args)
            else:
                result = {"status": "error", "message": f"Unsupported Loki action: {action}"}
        except (httpx.TimeoutException, httpcore.TimeoutException) as exc:
            result = {
                "status": "degraded",
                "message": f"Loki query timeout: {exc}",
            }
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}

        result.update({"tool": "loki", "action": action, "args": args, "dry_run": False})
        return result

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

    @staticmethod
    def _build_time_range(last_minutes: int) -> tuple[int, int]:
        end_ns = int(time.time() * 1e9)
        start_ns = end_ns - last_minutes * 60 * int(1e9)
        return start_ns, end_ns

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.user and self.password:
            import base64
            basic = f"{self.user}:{self.password}"
            headers["Authorization"] = "Basic " + base64.b64encode(basic.encode()).decode()
        return headers

    def _request_once(self, url: str, params: dict[str, str], headers: dict[str, str], timeout: httpx.Timeout, limits: httpx.Limits) -> dict[str, Any]:
        with httpx.Client(timeout=timeout, limits=limits) as client:
            response = client.get(url, params=params, headers=headers)
            logger.info(f"[GRAFANA-API] Response {response.status_code} {response.text[:300]}")
            response.raise_for_status()
            return response.json()

    def _request(self, logql: str, last_minutes: int, limit: int) -> dict[str, Any]:
        start_ns, end_ns = self._build_time_range(last_minutes)

        params = {
            "query": logql,
            "start": str(start_ns),
            "end": str(end_ns),
            "limit": str(limit),
            "direction": "backward",
        }

        headers = self._build_headers()

        url = f"{self.base_url}/loki/api/v1/query_range"
        logger.info(f"[GRAFANA-API] GET {url} params={params}")
        timeout = httpx.Timeout(
            connect=self.timeout_connect,
            read=self.timeout_read,
            write=self.timeout_connect,
            pool=self.timeout_connect,
        )
        
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
        last_timeout_exc: Exception | None = None
        data: dict[str, Any] | None = None
        for attempt in range(1, self.retry_max_attempts + 1):
            try:
                data = self._request_once(url, params, headers, timeout, limits)
                break
            except (httpx.TimeoutException, httpcore.TimeoutException) as exc:
                last_timeout_exc = exc
                if attempt >= self.retry_max_attempts:
                    raise
                sleep_s = 0.5 * attempt
                logger.warning(
                    "[GRAFANA-API] timeout on attempt %d/%d for %s; retry in %.1fs (%s)",
                    attempt,
                    self.retry_max_attempts,
                    logql,
                    sleep_s,
                    exc,
                )
                time.sleep(sleep_s)
        else:
            if last_timeout_exc:
                raise last_timeout_exc
            raise RuntimeError("Loki request failed without explicit exception")

        if data is None:
            raise RuntimeError("Loki returned no data")

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
