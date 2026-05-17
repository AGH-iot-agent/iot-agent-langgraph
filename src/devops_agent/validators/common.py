from __future__ import annotations

import logging
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def fetch_base_text(adapter, repo: str, path: str, branch: str) -> str | None:
    result = adapter.run(
        "github",
        "get_file_content",
        {"repo": repo, "path": path, "ref": branch},
        dry_run=False,
    )
    if result.get("status") == "ok" and result.get("content"):
        return result["content"]
    logger.warning("[VALIDATOR] Could not fetch base %s from %s@%s", path, repo, branch)
    return None


def apply_text_patch(base_text: str, original_snippet: str | None, fixed_content: str) -> tuple[str, bool]:
    if not original_snippet:
        # Explicit full-file replacement mode: accepted only when snippet is intentionally omitted.
        return fixed_content, True

    if original_snippet in base_text:
        return base_text.replace(original_snippet, fixed_content, 1), True

    orig_lines = [line.rstrip() for line in original_snippet.splitlines()]
    curr_lines = base_text.splitlines()
    for idx in range(len(curr_lines) - len(orig_lines) + 1):
        window = [line.rstrip() for line in curr_lines[idx: idx + len(orig_lines)]]
        if window == orig_lines:
            patched_lines = curr_lines[:idx] + fixed_content.splitlines() + curr_lines[idx + len(orig_lines):]
            patched = "\n".join(patched_lines) + ("\n" if base_text.endswith("\n") else "")
            return patched, True

    logger.warning("[VALIDATOR] original_snippet not found in base")
    return base_text, False


def safe_load_documents(yaml_content: str) -> tuple[list[Any], str | None]:
    try:
        docs = list(yaml.safe_load_all(yaml_content))
    except yaml.YAMLError as exc:
        return [], str(exc)
    return docs, None
