from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

import pytest
import yaml

from devops_agent.adapters.k8s_adapter import K8sAdapter
from devops_agent.nodes.sandbox_validator import route_after_validation
from devops_agent.validators.common import apply_text_patch
from devops_agent.validators.orchestrator import validate_fix_proposal

SMB_CHART_URI = os.environ.get(
    "TEST_SMB_CHART_URI",
    "smb://192.168.191.208/localshare/iot-agent/helm-charts/iot-agent-login-screen/deployment-1.0.0-test.tgz",
)
TARGET_NAMESPACE = os.environ.get("KUBERNETES_NAMESPACE", "iotag-sbx")


@dataclass
class PreparedChart:
    chart_dir: str
    cleanup_dirs: list[str]
    values_path: str
    modified_values: str


class _ValidatorIntegrationAdapter:
    def __init__(self, k8s: K8sAdapter) -> None:
        self._k8s = k8s

    def run(self, service: str, tool: str, params: dict, dry_run: bool = False) -> dict:
        if service == "github" and tool == "get_file_content":
            return {"status": "ok", "content": ""}

        if service == "kubernetes":
            return self._k8s.run(tool, params, dry_run=dry_run)

        return {"status": "error", "message": f"Unsupported service/tool: {service}/{tool}"}


def _assert_runtime_preconditions(k8s_adapter: K8sAdapter) -> None:
    missing = [binary for binary in ("helm", "kubectl", "curl") if not shutil.which(binary)]
    assert not missing, f"Missing required binaries in PATH: {', '.join(missing)}"

    cluster_check = subprocess.run(
        ["kubectl", "version", "--short", "--request-timeout=8s"],
        capture_output=True,
        text=True,
        timeout=20,
        env=os.environ,
    )
    if cluster_check.returncode != 0 and "unknown flag: --short" in (cluster_check.stderr or ""):
        cluster_check = subprocess.run(
            ["kubectl", "version", "--request-timeout=8s"],
            capture_output=True,
            text=True,
            timeout=20,
            env=os.environ,
        )

    assert cluster_check.returncode == 0, (
        "Kubernetes cluster preflight failed. "
        f"stdout={cluster_check.stdout.strip()} stderr={cluster_check.stderr.strip()}"
    )

    # SMB endpoint must be reachable and accessible — no skips, only pass or fail.
    try:
        downloaded_file, tmp_dir = k8s_adapter._download_smb_chart(SMB_CHART_URI)
    except RuntimeError as exc:
        pytest.fail(
            f"SMB preflight failed. Set SMB_USERNAME/SMB_PASSWORD if the share requires auth. "
            f"Details: {exc}"
        )

    try:
        assert os.path.exists(downloaded_file), "SMB chart was not downloaded"
        assert os.path.getsize(downloaded_file) > 0, "Downloaded SMB chart is empty"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _pick_values_file(chart_dir: str) -> str:
    candidates = [
        "values-sbx.yaml",
        "values-dev.yaml",
        "values.yaml",
    ]
    all_values = [
        path
        for path in Path(chart_dir).rglob("*.yaml")
        if path.name.lower().startswith("values")
    ]
    assert all_values, f"No values*.yaml files found in chart: {chart_dir}"

    for candidate in candidates:
        for path in all_values:
            if path.name == candidate:
                return str(path)

    return str(all_values[0])


def _build_modified_values(values_text: str) -> str:
    lines = values_text.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line:
            continue

        candidate_line = line
        patched_line = f"{candidate_line} # smb-integration-test"
        patched_text, replaced = apply_text_patch(values_text, candidate_line, patched_line)
        if not replaced:
            continue

        try:
            yaml.safe_load(patched_text)
            return patched_text
        except yaml.YAMLError:
            continue

    pytest.fail("Could not build a valid modified values file by changing an existing line")


def _list_temp_chart_dirs() -> set[str]:
    return set(glob.glob("/tmp/helm-chart-smb-*")) | set(glob.glob("/tmp/helm-chart-extract-*"))


@pytest.fixture(scope="session")
def k8s_adapter() -> K8sAdapter:
    return K8sAdapter()


@pytest.fixture(scope="session")
def preflight_requirements(k8s_adapter: K8sAdapter) -> None:
    _assert_runtime_preconditions(k8s_adapter)


@pytest.fixture(scope="module")
def prepared_chart(k8s_adapter: K8sAdapter) -> Generator[PreparedChart, None, None]:
    prepared: PreparedChart | None = None
    cleanup_dirs: list[str] = []

    try:
        chart_dir, cleanup_dirs = k8s_adapter._resolve_chart_source(SMB_CHART_URI)
        values_path = _pick_values_file(chart_dir)
        values_text = Path(values_path).read_text(encoding="utf-8")
        modified_values = _build_modified_values(values_text)

        prepared = PreparedChart(
            chart_dir=chart_dir,
            cleanup_dirs=cleanup_dirs,
            values_path=values_path,
            modified_values=modified_values,
        )
        yield prepared
    finally:
        for directory in cleanup_dirs:
            if directory and os.path.isdir(directory):
                shutil.rmtree(directory, ignore_errors=True)


@pytest.mark.integration
def test_smb_unpack_modify_and_full_dry_run(
    preflight_requirements: None,
    k8s_adapter: K8sAdapter,
    prepared_chart: PreparedChart,
) -> None:
    unique = str(int(time.time()))[-8:]

    result = k8s_adapter.helm_validate_deployability(
        release_name=f"login-screen-it-{unique}",
        service_name="iot-agent-login-screen",
        namespace=TARGET_NAMESPACE,
        chart=prepared_chart.chart_dir,
        values_override=prepared_chart.modified_values,
    )

    assert result.get("status") == "ok", result
    assert result.get("manifests"), "Expected rendered manifests from helm template"
    assert result.get("helm_upgrade_dry_run_output"), "Expected helm upgrade --dry-run output"


@pytest.mark.integration
def test_smb_invalid_uri_returns_error(preflight_requirements: None, k8s_adapter: K8sAdapter) -> None:
    
    result = k8s_adapter.helm_validate_deployability(
        release_name="login-screen-it-negative",
        service_name="iot-agent-login-screen",
        namespace=TARGET_NAMESPACE,
        chart="smb://192.168.191.208/localshare/iot-agent/docker-local/does-not-exist/missing-chart.tar",
        values_override="{}\n",
    )

    assert result.get("status") == "error", result
    message = str(result.get("message", ""))
    assert "failed to fetch SMB chart source" in message or "helm upgrade --install --dry-run failed" in message


@pytest.mark.integration
def test_smb_temporary_directories_are_cleaned_after_dry_run(
    preflight_requirements: None,
    k8s_adapter: K8sAdapter,
) -> None:
    before = _list_temp_chart_dirs()
    result = k8s_adapter.helm_template_render(name="cleanup-check", chart=SMB_CHART_URI, values_override="{}\n")
    assert result.get("status") == "ok", result

    after = _list_temp_chart_dirs()
    leaked = sorted(after - before)
    assert not leaked, f"Temporary SMB/extract directories were not cleaned: {leaked}"


@pytest.mark.integration
def test_validate_fix_proposal_uses_smb_chart_source(
    preflight_requirements: None,
    monkeypatch: pytest.MonkeyPatch,
    k8s_adapter: K8sAdapter,
) -> None:
    from devops_agent.validators import helm_rules

    chart_dir, cleanup_dirs = k8s_adapter._resolve_chart_source(SMB_CHART_URI)
    try:
        values_path = _pick_values_file(chart_dir)
        values_text = Path(values_path).read_text(encoding="utf-8")
        modified_values = _build_modified_values(values_text)

        # HELM chart source is read as a module global by helm_rules.
        monkeypatch.setattr(helm_rules, "_HELM_CHART_SOURCE", SMB_CHART_URI)

        adapter = _ValidatorIntegrationAdapter(k8s_adapter)
        proposal = {
            "files": [
                {
                    "path": "Helm/values-sbx.yaml",
                    "original_snippet": "",
                    "content": modified_values,
                }
            ],
            "root_cause": "integration smb check",
        }
        context = {
            "namespace": TARGET_NAMESPACE,
            "repo_full_name": "AGH-iot-agent/iot-agent-login-screen",
            "branch": "main",
            "release_name": f"login-screen-validator-{str(int(time.time()))[-8:]}",
            "chart_type": "deployment",
        }

        result = validate_fix_proposal(adapter, proposal=proposal, context=context)
        assert result.get("passed") is True, result
        assert "details" in result and result["details"], result
        assert result["details"][0].get("patchable") is True, result
        assert "helm deployability validation OK" in result.get("output", ""), result
    finally:
        for directory in cleanup_dirs:
            if directory and os.path.isdir(directory):
                shutil.rmtree(directory, ignore_errors=True)


def test_route_after_validation_blocks_pr_on_failed_final_attempt() -> None:
    state = {
        "validation_result": {"passed": False, "errors": ["boom"]},
        "fix_attempt": 3,
        "max_fix_attempts": 3,
        "event_kind": "github_issue",
        "ci_fix_proposal": {"files": [{"path": "Helm/values-sbx.yaml", "content": "x: y"}]},
    }

    assert route_after_validation(state) == "finalizer"
