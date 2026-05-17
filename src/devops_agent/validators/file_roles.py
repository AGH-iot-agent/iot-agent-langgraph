from __future__ import annotations

from pathlib import Path

YAML_EXTENSIONS: tuple[str, str] = (".yaml", ".yml")


def is_yaml_file(path: str) -> bool:
    return path.lower().endswith(YAML_EXTENSIONS)


def is_helm_values_file(path: str) -> bool:
    lowered_path = path.lower()
    lowered_name = Path(path).name.lower()
    return (
        lowered_name.startswith("values") and lowered_path.endswith(YAML_EXTENSIONS)
    ) or (
        "/helm/" in lowered_path and lowered_name.endswith(YAML_EXTENSIONS)
    )


def classify_file_role(path: str) -> str:
    if is_helm_values_file(path):
        return "helm_values"
    if is_yaml_file(path):
        return "kubernetes_manifest"
    return "non_yaml"
