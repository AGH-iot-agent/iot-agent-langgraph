from __future__ import annotations

from typing import Any

from devops_agent.validators.common import safe_load_documents


def _document_kind(document: Any) -> str:
    if isinstance(document, dict):
        kind = document.get("kind", "")
        return str(kind).strip() or "unknown"
    return "unknown"


def _document_name(document: Any) -> str:
    if not isinstance(document, dict):
        return ""
    metadata = document.get("metadata") or {}
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("name", "")).strip()


def _validate_common_manifest_fields(document: Any, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return errors

    kind = _document_kind(document)
    name = _document_name(document)

    if kind == "unknown":
        return errors
    if not name:
        errors.append(f"{path}: {kind} is missing metadata.name")

    metadata = document.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        errors.append(f"{path}: {kind} metadata must be a mapping")

    return errors


def _validate_string_mapping(
    mapping: Any,
    path: str,
    resource_label: str,
    field_name: str,
    value_description: str,
) -> list[str]:
    errors: list[str] = []
    if mapping is None:
        return errors
    if not isinstance(mapping, dict):
        errors.append(f"{path}: {resource_label} {field_name} must be a mapping")
        return errors

    for key, value in mapping.items():
        if not isinstance(key, str):
            errors.append(f"{path}: {resource_label} {field_name} key {key!r} must be a string")
        if not isinstance(value, str):
            errors.append(
                f"{path}: {resource_label} {field_name}['{key}'] must be a {value_description}, got {type(value).__name__}"
            )

    return errors


def _validate_configmap_document(document: Any, path: str) -> list[str]:
    if not isinstance(document, dict):
        return []

    errors: list[str] = []
    errors.extend(_validate_string_mapping(document.get("data"), path, "ConfigMap", "data", "string"))
    errors.extend(
        _validate_string_mapping(
            document.get("binaryData"), path, "ConfigMap", "binaryData", "base64 string"
        )
    )
    return errors


def _validate_secret_document(document: Any, path: str) -> list[str]:
    if not isinstance(document, dict):
        return []

    errors: list[str] = []
    errors.extend(_validate_string_mapping(document.get("data"), path, "Secret", "data", "string"))
    errors.extend(_validate_string_mapping(document.get("stringData"), path, "Secret", "stringData", "string"))
    return errors


def validate_manifest_content(adapter, content: str, path: str, namespace: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    outputs: list[str] = []

    documents, parse_error = safe_load_documents(content)
    if parse_error:
        errors.append(f"{path}: YAML parse failed: {parse_error}")
        return errors, outputs

    for index, document in enumerate(documents, start=1):
        if document is None:
            outputs.append(f"{path}: document {index} is empty - skipped")
            continue

        if isinstance(document, dict):
            kind = _document_kind(document)
            errors.extend(_validate_common_manifest_fields(document, path))

            if kind == "ConfigMap":
                errors.extend(_validate_configmap_document(document, path))
                outputs.append(f"{path}: ConfigMap document {index} checked")
            elif kind == "Secret":
                errors.extend(_validate_secret_document(document, path))
                outputs.append(f"{path}: Secret document {index} checked")
            elif kind != "unknown":
                outputs.append(f"{path}: {kind} document {index} checked")
            else:
                outputs.append(f"{path}: document {index} parsed as generic YAML")
        else:
            outputs.append(f"{path}: document {index} is not a Kubernetes mapping - semantic checks only")

    apply_result = adapter.run(
        "kubernetes",
        "apply_manifest_dryrun",
        {"manifest_yaml": content, "namespace": namespace},
        dry_run=False,
    )
    if apply_result.get("status") != "ok":
        errors.append(f"kubectl apply --dry-run failed for {path}: {apply_result.get('message', '')}")
    else:
        outputs.append(f"kubectl apply --dry-run=server OK for {path}")

    return errors, outputs
