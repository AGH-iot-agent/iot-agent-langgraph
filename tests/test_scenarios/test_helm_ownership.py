from __future__ import annotations

from devops_agent.adapters.k8s_adapter import (
    K8sAdapter,
    existing_release_from_ownership_error,
    is_helm_isolation_error,
)


def test_ownership_error_extracts_existing_release_name() -> None:
    message = (
        'Error: Unable to continue with install: Service "iot-agent-login-screen" '
        'in namespace "iotag-sbx" exists and cannot be imported into the current '
        "release: invalid ownership metadata; annotation validation error: "
        'key "meta.helm.sh/release-name" must equal "live-e2e-88347442": '
        'current value is "iot-agent-login-screen"'
    )
    assert existing_release_from_ownership_error(message) == "iot-agent-login-screen"


def test_ownership_error_returns_none_when_unrelated() -> None:
    assert existing_release_from_ownership_error("YAML parse error") is None


def test_resolve_dry_run_prefers_existing_service_release() -> None:
    adapter = K8sAdapter()
    adapter._helm_release_names = lambda _ns: {"iot-agent-login-screen"}  # type: ignore[method-assign]
    resolved = adapter._resolve_dry_run_release_name(
        namespace="iotag-sbx",
        release_name="live-e2e-88347442",
        service_name="iot-agent-login-screen",
    )
    assert resolved == "iot-agent-login-screen"


def test_pending_upgrade_is_isolation_error() -> None:
    assert is_helm_isolation_error(
        "Error: UPGRADE FAILED: another operation (install/upgrade/rollback) is in progress"
    )
    assert is_helm_isolation_error(
        'invalid ownership metadata; key "meta.helm.sh/release-name" must equal "x"'
    )
    assert not is_helm_isolation_error("YAML parse error on values-sbx.yaml")
