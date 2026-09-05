from __future__ import annotations

import pytest

from devops_agent.nodes.ci_fixer import (
    _classify_issue,
    _coerce_proposal_files,
    _materialize_proposal_files,
    ISSUE_TYPE_HELM_MANIFEST,
    ISSUE_TYPE_MAVEN_POM,
)
from devops_agent.validators.common import (
    apply_text_patch,
    apply_unique_yaml_key_patch,
    looks_like_complete_file,
    resolve_proposed_text,
    strip_unified_diff_fences,
)
from devops_agent.validators.file_roles import namespace_for_helm_values_path
from devops_agent.validators.helm_rules import validate_helm_values_file
from devops_agent.validators.orchestrator import validate_fix_proposal


_HELM_VALUES = """\
name: iot-agent-login-screen
replicaCount: "not-a-number"
image:
  repository: ghcr.io/example/login
  tag: latest
istio:
  virtualService:
    enabled: true
service:
  port: 80
global:
  namespace: iotag-sbx
minReadySeconds: 0
revisionHistoryLimit: 3
livenessProbe:
  enabled: true
  path: /health
readinessProbe:
  enabled: true
  path: /ready
resources:
  limits:
    cpu: 100m
"""


class _FakeGitHubAdapter:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.calls: list[tuple[str, str]] = []

    def run(self, service: str, tool: str, params: dict, dry_run: bool = False) -> dict:
        self.calls.append((service, tool))
        if service == "github" and tool == "get_file_content":
            path = params.get("path", "")
            content = self.files.get(path)
            if content is None:
                return {"status": "error", "message": f"missing {path}"}
            return {"status": "ok", "content": content}
        if service == "kubernetes":
            return {"status": "ok", "output": "ok", "message": "ok"}
        return {"status": "error", "message": f"unexpected {service}/{tool}"}


def test_coerce_top_level_path_content_into_files_not_regressed() -> None:
    proposal = _coerce_proposal_files(
        {"path": "Helm/values-sbx.yaml", "content": "name: login\n"}
    )
    assert proposal["files"] == [
        {"path": "Helm/values-sbx.yaml", "content": "name: login\n"}
    ]


def test_strip_unified_diff_fences_drops_hunk_prefixes() -> None:
    snippet = "--- a/Helm/values-sbx.yaml\n+++ b/Helm/values-sbx.yaml\n@@ -1,1 +1,1 @@\n-replicaCount: \"1\"\n+replicaCount: 1\n"
    stripped = strip_unified_diff_fences(snippet)
    assert "replicaCount: \"1\"" in stripped or 'replicaCount: "1"' in stripped
    assert not stripped.startswith("-")


def test_strip_concatenated_diff_headers_without_newline() -> None:
    snippet = '--- a/Helm/values-sbx.yaml+++ b/Helm/values-sbx.yaml\n@@ -1 +1 @@\n-replicaCount: "not-a-number"\n+replicaCount: 1\n'
    stripped = strip_unified_diff_fences(snippet)
    assert "replicaCount" in stripped
    assert "--- a/" not in stripped
    assert "+++ b/" not in stripped


def test_apply_text_patch_snippet_miss_does_not_mutate_base() -> None:
    patched, found = apply_text_patch(_HELM_VALUES, 'replicaCount: "1"', "replicaCount: 1")
    assert found is False
    assert patched == _HELM_VALUES


def test_apply_text_patch_full_file_when_snippet_omitted() -> None:
    patched, found = apply_text_patch(_HELM_VALUES, "", "replicaCount: 1\n")
    assert found is True
    assert patched == "replicaCount: 1\n"


def test_unique_key_patch_fixes_hallucinated_replicacount_value() -> None:
    patched, found = apply_unique_yaml_key_patch(
        _HELM_VALUES, 'replicaCount: "1"', "replicaCount: 1"
    )
    assert found is True
    assert 'replicaCount: 1\n' in patched
    assert 'replicaCount: "not-a-number"' not in patched
    assert "global:" in patched


def test_unique_key_patch_refuses_ambiguous_key() -> None:
    base = "name: a\nname: b\n"
    patched, found = apply_unique_yaml_key_patch(base, "name: a", "name: z")
    assert found is False
    assert patched == base


def test_resolve_proposed_text_prefers_full_file_over_mismatched_snippet() -> None:
    full_fixed = _HELM_VALUES.replace('replicaCount: "not-a-number"', "replicaCount: 1")
    text, patchable, reason = resolve_proposed_text(
        _HELM_VALUES, 'replicaCount: "1"', full_fixed
    )
    assert patchable is True
    assert reason == "full_file_replacement"
    assert text == full_fixed


def test_resolve_proposed_text_snippet_miss_without_full_file_is_not_patchable() -> None:
    text, patchable, reason = resolve_proposed_text(
        _HELM_VALUES,
        "this snippet is not in the file at all",
        "also not a complete helm values file",
    )
    assert patchable is False
    assert reason == "original_snippet_not_found"
    assert text == _HELM_VALUES


def test_resolve_proposed_text_refuses_tiny_content_as_full_file() -> None:
    text, patchable, reason = resolve_proposed_text(_HELM_VALUES, "", "replicaCount: 1\n")
    assert patchable is False
    assert reason == "refusing_snippet_as_full_file"
    assert text == _HELM_VALUES


def test_looks_like_complete_file_detects_helm_values() -> None:
    assert looks_like_complete_file(_HELM_VALUES, _HELM_VALUES) is True
    assert looks_like_complete_file("replicaCount: 1\n", _HELM_VALUES) is False


def test_namespace_for_helm_values_path() -> None:
    assert namespace_for_helm_values_path("Helm/values-sbx.yaml", "iotag-dev") == "iotag-sbx"
    assert namespace_for_helm_values_path("Helm/values-dev.yaml", "iotag-sbx") == "iotag-dev"
    assert namespace_for_helm_values_path("Helm/values.yaml", "iotag-sbx") == "iotag-sbx"


def test_validate_helm_values_file_snippet_miss_fails_before_helm() -> None:
    adapter = _FakeGitHubAdapter({"Helm/values-sbx.yaml": _HELM_VALUES})
    errors, outputs, meta = validate_helm_values_file(
        adapter,
        path="Helm/values-sbx.yaml",
        repo="org/login-screen",
        branch="test/scenario_02",
        namespace="iotag-sbx",
        service_name="login",
        release_name="login",
        chart_type="deployment",
        original_snippet='replicaCount: "1"\nimage:\n  tag: no-such-tag\n',
        content="replicaCount: 1\nimage:\n  tag: still-a-snippet\n",
    )
    assert meta["patchable"] is False
    assert meta["patch_reason"] == "original_snippet_not_found"
    assert any("not safely patchable" in err for err in errors)
    assert any("patchability check failed" in line for line in outputs)
    assert ("kubernetes", "helm_validate_deployability") not in adapter.calls


def test_validate_helm_values_file_full_file_skips_snippet_match(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("devops_agent.validators.helm_rules._HELM_CHART_SOURCE", str(tmp_path))
    adapter = _FakeGitHubAdapter({"Helm/values-sbx.yaml": _HELM_VALUES})
    errors, outputs, meta = validate_helm_values_file(
        adapter,
        path="Helm/values-sbx.yaml",
        repo="org/login-screen",
        branch="test/scenario_02",
        namespace="iotag-sbx",
        service_name="login",
        release_name="login",
        chart_type="deployment",
        original_snippet='replicaCount: "1"',
        content=_HELM_VALUES.replace('replicaCount: "not-a-number"', "replicaCount: 1"),
    )
    assert meta["patchable"] is True
    assert meta["patch_reason"] == "full_file_replacement"
    assert ("kubernetes", "helm_validate_deployability") in adapter.calls
    assert not any("not safely patchable" in err for err in errors)


def test_validate_fix_proposal_uses_iotag_sbx_for_values_sbx_even_if_context_is_dev() -> None:
    adapter = _FakeGitHubAdapter({"Helm/values-sbx.yaml": _HELM_VALUES})
    result = validate_fix_proposal(
        adapter,
        proposal={
            "files": [{
                "path": "Helm/values-sbx.yaml",
                "original_snippet": "this is not in the file",
                "content": "replicaCount: 1\n",
            }]
        },
        context={
            "namespace": "iotag-dev",
            "repo_full_name": "org/login-screen",
            "branch": "test/scenario_02",
        },
    )
    assert result["passed"] is False
    assert result["namespace"] == "iotag-sbx"
    assert result["details"][0]["namespace"] == "iotag-sbx"


def test_classify_replicacount_pdb_error_as_helm_manifest() -> None:
    logs = (
        "Error: template: deployment/templates/pdb.yaml:4:14: executing "
        '"deployment/templates/pdb.yaml" at <gt .Values.replicaCount 1.0>: '
        "error calling gt: incompatible types for comparison\n"
        "helm.go:84: failed to render Helm/values-sbx.yaml"
    )
    assert _classify_issue(logs) == ISSUE_TYPE_HELM_MANIFEST


def test_classify_unrelated_maven_error_is_not_helm() -> None:
    logs = "Could not resolve dependencies: could not find artifact com.example:lib:1.0"
    assert _classify_issue(logs) == ISSUE_TYPE_MAVEN_POM


def test_classify_helm_upgrade_timeout_as_helm_manifest() -> None:
    logs = (
        "Current deployment image: iot-agent-login-screen:abc\n"
        "level=WARN msg=\"upgrade failed\" name=iot-agent-login-screen "
        "error=\"resource Deployment/iotag-sbx/iot-agent-login-screen not ready. "
        "status: InProgress, message: Pending termination: 1\\ncontext deadline exceeded\"\n"
        "Error: UPGRADE FAILED: resource Deployment/iotag-sbx/iot-agent-login-screen not ready. "
        "status: InProgress, message: Pending termination: 1\n"
        "context deadline exceeded\n"
    )
    assert _classify_issue(logs) == ISSUE_TYPE_HELM_MANIFEST


def test_validate_helm_values_file_unique_key_replicacount_is_patchable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("devops_agent.validators.helm_rules._HELM_CHART_SOURCE", str(tmp_path))
    adapter = _FakeGitHubAdapter({"Helm/values-sbx.yaml": _HELM_VALUES})
    errors, outputs, meta = validate_helm_values_file(
        adapter,
        path="Helm/values-sbx.yaml",
        repo="org/login-screen",
        branch="test/scenario_02",
        namespace="iotag-sbx",
        service_name="login",
        release_name="login",
        chart_type="deployment",
        original_snippet='replicaCount: "1"',
        content="replicaCount: 1",
    )
    assert meta["patchable"] is True
    assert meta["patch_reason"] == "unique_key_replaced"
    assert not any("not safely patchable" in err for err in errors)
    assert ("kubernetes", "helm_validate_deployability") in adapter.calls


def test_materialize_snippet_proposal_into_full_file() -> None:
    execution_results = [{
        "action": "get_file_content",
        "args": {"path": "Helm/values-sbx.yaml"},
        "result": {"status": "ok", "content": _HELM_VALUES},
    }]
    proposal = _materialize_proposal_files(
        {
            "files": [{
                "path": "Helm/values-sbx.yaml",
                "original_snippet": 'replicaCount: "1"',
                "fixed_snippet": "replicaCount: 1",
            }]
        },
        execution_results,
    )
    entry = proposal["files"][0]
    assert entry["original_snippet"] == ""
    assert "replicaCount: 1" in entry["content"]
    assert 'replicaCount: "not-a-number"' not in entry["content"]
    assert "global:" in entry["content"]
    assert "istio:" in entry["content"]
