from __future__ import annotations

from unittest.mock import patch

from devops_agent.preflight import collect_runtime_dependencies, runtime_preflight_report


def test_collect_runtime_dependencies_includes_core_runtime_tools(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_ORG", "AGH-iot-agent")
    monkeypatch.setenv("KUBERNETES_NAMESPACE", "iotag-dev")

    deps = collect_runtime_dependencies()
    names = {dep.name for dep in deps}

    assert "gh" in names
    assert "kubectl" in names
    assert "helm" in names
    assert "smbclient" in names


def test_runtime_preflight_report_flags_missing_required_binary(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_ORG", "AGH-iot-agent")
    monkeypatch.setenv("KUBERNETES_NAMESPACE", "iotag-dev")

    with patch("devops_agent.preflight.shutil.which") as which_mock:
        which_mock.side_effect = lambda name: None if name == "helm" else f"/usr/bin/{name}"
        report = runtime_preflight_report()

    assert report["ok"] is False
    assert report["missing_required"] == ["helm"]
    helm_check = next(check for check in report["checks"] if check["name"] == "helm")
    assert helm_check["ok"] is False