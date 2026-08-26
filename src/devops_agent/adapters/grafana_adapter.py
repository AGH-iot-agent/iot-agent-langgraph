from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GRAFANA_URL_DEFAULT = os.getenv("GRAFANA_URL")
GRAFANA_API_KEY = os.getenv("GRAFANA_API_KEY")
GRAFANA_TIMEOUT = int(os.getenv("GRAFANA_TIMEOUT", "20"))


@dataclass
class GrafanaAdapter:
    base_url: str = field(default_factory=lambda: GRAFANA_URL_DEFAULT.rstrip("/") if GRAFANA_URL_DEFAULT else "")
    api_key: str | None = field(default_factory=lambda: GRAFANA_API_KEY)
    timeout: int = field(default_factory=lambda: GRAFANA_TIMEOUT)

    def search_dashboards(self, query: str = "", search_type: str = "dash-db") -> dict[str, Any]:
        params: dict[str, str] = {"type": search_type}
        if query:
            params["query"] = query
        return self._request("GET", "/api/search", params=params)

    def get_dashboard(self, uid: str) -> dict[str, Any]:
        if not uid:
            return {"status": "error", "message": "uid arg is required for Grafana dashboard lookup"}
        return self._request("GET", f"/api/dashboards/uid/{uid}")

    def list_folders(self) -> dict[str, Any]:
        return self._request("GET", "/api/folders")

    def import_dashboard(
        self,
        dashboard: dict[str, Any],
        folder_uid: str | None = None,
        overwrite: bool = True,
        message: str = "Imported by iot-agent-langgraph",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "dashboard": dashboard,
            "overwrite": overwrite,
            "message": message,
        }
        if folder_uid:
            payload["folderUid"] = folder_uid
        return self._request("POST", "/api/dashboards/db", json_body=payload)

    def run(self, action: str, args: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        if dry_run:
            return {
                "tool": "grafana",
                "action": action,
                "args": args,
                "status": "simulated",
                "dry_run": True,
                "message": "GrafanaAdapter dry-run: no real request performed.",
            }

        if not self.base_url:
            return {
                "tool": "grafana",
                "action": action,
                "args": args,
                "status": "error",
                "message": "GRAFANA_URL env variable not set.",
            }

        try:
            if action in ("search_dashboards", "search"):
                result = self.search_dashboards(**args)
            elif action in ("get_dashboard", "get_dashboard_by_uid"):
                result = self.get_dashboard(**args)
            elif action in ("list_folders", "get_folders"):
                result = self.list_folders()
            elif action in ("import_dashboard", "upsert_dashboard"):
                result = self.import_dashboard(**args)
            else:
                result = {"status": "error", "message": f"Unsupported Grafana action: {action}"}
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}

        result.update({"tool": "grafana", "action": action, "args": args, "dry_run": False})
        return result

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.base_url:
            return {"status": "error", "message": "GRAFANA_URL env variable not set."}

        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(method, url, params=params, json=json_body, headers=self._headers())
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.exception("[GrafanaAdapter] request failed: %s %s", method, path)
            return {"status": "error", "message": str(exc)}

        return {"status": "ok", "data": data}