"""Best-effort cleanup of leftover test Helm releases in iotag-sbx."""
from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

_TEST_RELEASE_PREFIXES = (
    "live-e2e-",
    "ci-fix-it-",
    "ci-fix-broken-",
    "sbx-node-it-",
    "pr-dry-run-",
    "login-screen-it-",
    "login-screen-validator-",
    "ci-fix-it-test",
    "ci-fix-evidence-",
    "ci-fix-noerr-",
    "ci-fix-history-",
    "ci-fix-idempotent-",
)

_PROTECTED = frozenset(
    {
        "iot-agent-login-screen",
        "iot-agent-authentication",
        "iot-agent-gateway-api",
        "iot-agent-device-api",
        "iot-agent-alert-api",
        "iot-agent-logs",
        "iot-agent-dashboard-api",
    }
)


def uninstall_leftover_test_releases(namespace: str | None = None) -> list[str]:
    """Uninstall unique-name test releases. Never touches production service releases."""
    namespace = namespace or os.environ.get("TEST_CI_FIX_TARGET_NAMESPACE", "iotag-sbx")
    removed: list[str] = []
    try:
        listed = subprocess.run(
            ["helm", "list", "--namespace", namespace, "-q"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("helm list unavailable: %s", exc)
        return removed
    if listed.returncode != 0:
        logger.warning("helm list failed: %s", listed.stderr)
        return removed

    for name in (line.strip() for line in listed.stdout.splitlines() if line.strip()):
        if name in _PROTECTED:
            continue
        if not any(name == prefix or name.startswith(prefix) for prefix in _TEST_RELEASE_PREFIXES):
            continue
        result = subprocess.run(
            ["helm", "uninstall", name, "--namespace", namespace],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            removed.append(name)
            logger.info("uninstalled leftover test release %s in %s", name, namespace)
        else:
            logger.warning("failed to uninstall %s: %s", name, result.stderr)
    return removed
