from __future__ import annotations

from typing import Any

from devops_agent.validators.file_roles import classify_file_role, namespace_for_helm_values_path
from devops_agent.validators.helm_rules import validate_helm_values_file
from devops_agent.validators.manifest_rules import validate_manifest_content

import yaml
from yaml.parser import ParserError
from yaml.scanner import ScannerError

def validate_yaml_content(content: str, path: str) -> list[str]:
    errors: list[str] = []

    try:
        list(yaml.safe_load_all(content))
    except ScannerError as exc:
        mark = exc.problem_mark
        errors.append(
            f"{path}: invalid YAML syntax at line {mark.line + 1}, "
            f"column {mark.column + 1}: {exc.problem}"
        )
    except ParserError as exc:
        mark = exc.problem_mark
        errors.append(
            f"{path}: YAML parser error at line {mark.line + 1}, "
            f"column {mark.column + 1}: {exc.problem}"
        )
    except Exception as exc:
        errors.append(
            f"{path}: YAML validation failed: {exc}"
        )

    return errors

def _validate_file_entry(
    adapter,
    *,
    file_entry: dict[str, Any],
    repo: str,
    branch: str,
    namespace: str,
    service_name: str,
    release_name: str,
    chart_type: str,
) -> tuple[list[str], list[str], dict[str, Any]]:
    path: str = file_entry.get("path", "")
    content: str = file_entry.get("content") or file_entry.get("fixed_snippet") or ""
    original_snippet: str = file_entry.get("original_snippet", "") or ""
    role = classify_file_role(path)

    detail: dict[str, Any] = {"path": path, "role": role}

    if not content:
        detail.update({"status": "error", "reason": "empty content"})
        return [f"{path}: empty content - skipping"], [], detail

    if role == "helm_values":
        file_namespace = namespace_for_helm_values_path(path, namespace)
        detail["namespace"] = file_namespace
        yaml_errors = validate_yaml_content(content, path)

        if yaml_errors:
            detail.update({
                "status": "error",
                "patchable": True,
                "patch_reason": "invalid_yaml",
                "rules": ["yaml_parse", "helm_deployability"],
                "errors": yaml_errors,
            })
            return yaml_errors, [], detail

        errors, outputs, patch_meta = validate_helm_values_file(
            adapter,
            path=path,
            repo=repo,
            branch=branch,
            namespace=file_namespace,
            service_name=service_name,
            release_name=release_name,
            chart_type=chart_type,
            original_snippet=original_snippet,
            content=content,
        )
        detail.update(patch_meta)
        detail["namespace"] = file_namespace
        detail["rules"] = ["helm_deployability"]
    elif role == "kubernetes_manifest":
        errors, outputs = validate_manifest_content(adapter, content, path, namespace)
        detail.update({"patchable": True, "patch_reason": "direct_manifest_validation"})
        detail["rules"] = ["yaml_parse", "kind_specific_checks", "server_dry_run"]
    else:
        errors = []
        outputs = [f"{path}: non-YAML file - skipping k8s validation"]
        detail.update({"patchable": True, "patch_reason": "non_yaml_skipped"})
        detail["rules"] = ["skip_non_yaml"]

    detail["status"] = "error" if errors else "ok"
    if errors:
        detail["errors"] = errors

    return errors, outputs, detail


def _proposal_file_entries(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    files = proposal.get("files")
    if isinstance(files, dict):
        return [files]
    if isinstance(files, list) and files:
        return files
    path = proposal.get("path")
    if isinstance(path, str) and path.strip():
        return [
            {
                "path": path,
                "content": proposal.get("content") or proposal.get("fixed_snippet") or "",
                "original_snippet": proposal.get("original_snippet") or "",
            }
        ]
    return files if isinstance(files, list) else []


def validate_fix_proposal(
    adapter,
    *,
    proposal: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    files: list[dict[str, Any]] = _proposal_file_entries(proposal)
    namespace: str = context.get("namespace", "iotag-sbx")
    repo: str = context.get("repo_full_name", "")
    branch: str = context.get("branch", "main")
    service_name: str = repo.split("/")[-1] or "ci-dry-run"
    release_name: str = context.get("release_name") or service_name
    chart_type: str = context.get("chart_type", "deployment")

    if not files:
        return {
            "passed": False,
            "errors": ["No files in fix proposal - nothing to validate."],
            "output": "",
            "details": [],
        }

    errors: list[str] = []
    outputs: list[str] = []
    details: list[dict[str, Any]] = []

    for file_entry in files:
        file_errors, file_outputs, detail = _validate_file_entry(
            adapter,
            file_entry=file_entry,
            repo=repo,
            branch=branch,
            namespace=namespace,
            service_name=service_name,
            release_name=release_name,
            chart_type=chart_type,
        )
        errors.extend(file_errors)
        outputs.extend(file_outputs)
        details.append(detail)

    namespaces_used = {
        str(detail.get("namespace"))
        for detail in details
        if detail.get("namespace")
    }
    result_namespace = namespace
    if "iotag-sbx" in namespaces_used:
        result_namespace = "iotag-sbx"
    elif namespaces_used:
        result_namespace = next(iter(namespaces_used))

    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "output": "\n".join(outputs),
        "details": details,
        "namespace": result_namespace,
    }
