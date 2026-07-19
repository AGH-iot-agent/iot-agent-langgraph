from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from devops_agent.validators.common import apply_text_patch, deep_merge, fetch_base_text

logger = logging.getLogger(__name__)

_WORKSPACE_ROOT = Path(__file__).parents[4]
_DEFAULT_CHART_DIR = _WORKSPACE_ROOT / "Universal-Kubernetes-Helm-Charts" / "charts" / "deployment"
_HELM_CHART_SOURCE = (
    os.environ.get("HELM_CHART_SOURCE")
    or os.environ.get("HELM_CHART_DIR")
    or str(_DEFAULT_CHART_DIR)
)

_CICD_INJECTED_VALUES: dict[str, Any] = {
    "image": {"repository": "ghcr.io/placeholder/placeholder", "tag": "ci"},
    "global": {"image": {"tag": "ci"}},
}


def _preflight_validation_environment(adapter, *, namespace: str, chart_path: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    outputs: list[str] = []

    is_smb_source = str(chart_path).lower().startswith("smb://")

    if not is_smb_source and not Path(chart_path).exists():
        errors.append(f"validation environment not ready: Helm chart path not found: {chart_path}")
        return errors, outputs

    if is_smb_source:
        outputs.append(f"validator preflight OK: chart source is smb URI ({chart_path})")
    else:
        outputs.append(f"validator preflight OK: chart path exists ({chart_path})")

    cluster_check = adapter.run(
        "kubernetes",
        "get_rollout_status",
        {"namespace": namespace},
        dry_run=False,
    )
    if cluster_check.get("status") != "ok":
        errors.append(
            "validation environment not ready: cannot access kubernetes rollout status "
            f"for namespace '{namespace}': {cluster_check.get('message', '')}"
        )
        return errors, outputs

    outputs.append(f"validator preflight OK: kubernetes reachable for namespace {namespace}")
    return errors, outputs


def _inject_ci_values_for_helm(values_text: str, service_name: str) -> str:
    try:
        values = yaml.safe_load(values_text) or {}
    except yaml.YAMLError:
        return values_text

    merged = deep_merge(values, _CICD_INJECTED_VALUES)
    # Only inject service name when the values file has no name at all.
    # If name is explicitly set to "" (empty string), do NOT override it —
    # Helm must see the empty value and fail with a clear required-field error.
    # This prevents the validator from masking a real deployment blocker.
    if not merged.get("name"):
        merged["name"] = service_name
    return yaml.dump(merged, default_flow_style=False, allow_unicode=True)


class LiteralDumper(yaml.SafeDumper):
    pass


def _repr_str(dumper, value):
    if value == "":
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str",
            "",
            style='"',
        )
    return dumper.represent_str(value)


LiteralDumper.add_representer(str, _repr_str)

def normalize_yaml_content(content: str) -> str:
    """Strip comments, blank lines and formatting from YAML while preserving semantics."""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        return content

    return yaml.dump(
        data,
        Dumper=LiteralDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).rstrip()
    
def validate_helm_values_file(
    adapter,
    *,
    path: str,
    repo: str,
    branch: str,
    namespace: str,
    service_name: str,
    release_name: str,
    chart_type: str,
    original_snippet: str,
    content: str,
) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    outputs: list[str] = []
    patch_meta: dict[str, Any] = {
        "patchable": True,
        "patch_reason": "snippet_replaced" if original_snippet else "full_file_replacement",
    }

    base_text = fetch_base_text(adapter, repo, path, branch) or ""
    text_patched, snippet_found = apply_text_patch(base_text, original_snippet, content)
    text_patched = normalize_yaml_content(text_patched)

    if original_snippet and not snippet_found:
        patch_meta["patchable"] = False
        patch_meta["patch_reason"] = "original_snippet_not_found"
        errors.append(
            f"{path}: original_snippet not found in base branch content - proposal is not safely patchable"
        )
        outputs.append(f"{path}: patchability check failed")
        return errors, outputs, patch_meta

    values_override = _inject_ci_values_for_helm(text_patched, service_name=service_name)

    chart_path = _HELM_CHART_SOURCE
    # If HELM_CHART_SOURCE is explicitly provided, treat it as authoritative
    # (can be local dir/file or remote smb:// URI).
    if "HELM_CHART_SOURCE" not in os.environ and chart_type and chart_type != "deployment":
        chart_path = str((Path(_HELM_CHART_SOURCE).parents[0] / chart_type).resolve())

    preflight_errors, preflight_outputs = _preflight_validation_environment(
        adapter,
        namespace=namespace,
        chart_path=chart_path,
    )
    outputs.extend(preflight_outputs)
    if preflight_errors:
        errors.extend(preflight_errors)
        logger.warning("[VALIDATOR] Helm preflight failed for %s: %s", path, preflight_errors)
        return errors, outputs, patch_meta

    validate_result = adapter.run(
        "kubernetes",
        "helm_validate_deployability",
        {
            "release_name": release_name,
            "service_name": service_name,
            "namespace": namespace,
            "chart": chart_path,
            "values_override": values_override,
            "image_repository": _CICD_INJECTED_VALUES["image"]["repository"],
            "image_tag": _CICD_INJECTED_VALUES["image"]["tag"],
        },
        dry_run=False,
    )

    if validate_result.get("status") != "ok":
        errors.append(f"helm deployability validation failed for {path}: {validate_result.get('message', '')}")
        return errors, outputs, patch_meta

    outputs.append(f"helm deployability validation OK for {path}")
    return errors, outputs, patch_meta
