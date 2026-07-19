from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger(__name__)


def _flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "true" if default else "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeDependency:
    name: str
    reason: str
    required: bool = True


def collect_runtime_dependencies() -> list[RuntimeDependency]:
    dependencies: list[RuntimeDependency] = []

    github_enabled = any(
        _flag(name)
        for name in (
            "MONITOR_GITHUB_ISSUES",
            "MONITOR_GITHUB_PRS",
            "MONITOR_GITHUB_CI_FAILURE",
            "MONITOR_GITHUB_PR_BUILDS",
        )
    ) or bool(os.environ.get("GITHUB_ORG"))

    k8s_enabled = _flag("MONITOR_K8S") or bool(os.environ.get("KUBERNETES_NAMESPACE"))
    prometheus_enabled = _flag("MONITOR_PROMETHEUS")
    loki_enabled = _flag("MONITOR_LOKI")

    if github_enabled:
        dependencies.append(
            RuntimeDependency(
                name="gh",
                reason="required for GitHub issue/PR/CI integration",
            )
        )

    if k8s_enabled or prometheus_enabled or loki_enabled:
        dependencies.append(
            RuntimeDependency(
                name="kubectl",
                reason="required for Kubernetes monitor and validation flows",
            )
        )

    dependencies.extend(
        [
            RuntimeDependency(
                name="helm",
                reason="required for Helm validation and deployability checks",
            ),
            RuntimeDependency(
                name="smbclient",
                reason="required for smb:// chart retrieval",
            ),
        ]
    )

    unique: dict[str, RuntimeDependency] = {}
    for dependency in dependencies:
        unique.setdefault(dependency.name, dependency)

    return list(unique.values())


def runtime_preflight_report() -> dict[str, Any]:
    dependencies = collect_runtime_dependencies()
    checks: list[dict[str, str | bool]] = []
    missing_required: list[str] = []

    for dependency in dependencies:
        resolved = shutil.which(dependency.name)
        ok = bool(resolved)
        checks.append(
            {
                "name": dependency.name,
                "ok": ok,
                "reason": dependency.reason,
                "path": resolved or "",
            }
        )
        if dependency.required and not ok:
            missing_required.append(dependency.name)

    return {
        "ok": not missing_required,
        "checks": checks,
        "missing_required": missing_required,
    }


def log_runtime_preflight(report: dict[str, Any]) -> None:
    for check in report["checks"]:
        if check["ok"]:
            logger.info(
                "[PREFLIGHT] dependency_ok name=%s path=%s reason=%s",
                check["name"],
                check["path"],
                check["reason"],
            )
        else:
            logger.error(
                "[PREFLIGHT] dependency_missing name=%s reason=%s",
                check["name"],
                check["reason"],
            )
