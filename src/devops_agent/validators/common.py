from __future__ import annotations

import logging
import re
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_HELM_VALUES_MARKERS = ("replicaCount", "image:", "global:", "istio:", "service:")
_YAML_KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_.-]+)\s*:")
# The LLM often quotes a key as if it were still commented out (``# replicaCount: "1"``)
# even though the base file has it live. Only the key lookup tolerates that marker.
_COMMENTED_KEY_RE = re.compile(r"^(\s*)#+\s*(?=[A-Za-z0-9_.-]+\s*:)")


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


_CONCAT_DIFF_HEADER_RE = re.compile(
    r"(---[^\n]*?)(\+\+\+[^\n]*)",
    re.MULTILINE,
)


def strip_unified_diff_fences(snippet: str | None) -> str:
    """Drop unified-diff headers / line prefixes when the whole snippet is a diff hunk.

    Also repairs the malformed ``--- a/file+++ b/file`` form (missing newline)
    that otherwise makes original_snippet unmatchable against the real file.
    """
    if not snippet:
        return ""
    repaired = _CONCAT_DIFF_HEADER_RE.sub(r"\1\n\2", snippet)
    lines = repaired.splitlines()
    body = [
        line
        for line in lines
        if not line.startswith(("+++", "---", "@@", "diff "))
    ]
    if not body:
        return ""

    prefixes = [line[:1] for line in body if line]
    looks_like_diff = (
        prefixes
        and all(prefix in "+- " for prefix in prefixes)
        and any(prefix in "+-" for prefix in prefixes)
    )
    if looks_like_diff:
        stripped = [(line[1:] if line[:1] in "+- " else line) for line in body]
        return "\n".join(stripped)
    return "\n".join(body) if body != lines else repaired


def _nonempty_line_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def looks_like_helm_values_file(text: str) -> bool:
    if not text or not text.strip():
        return False
    hits = sum(1 for marker in _HELM_VALUES_MARKERS if marker in text)
    return hits >= 3 and _nonempty_line_count(text) >= 20


def looks_like_complete_file(candidate: str, base_text: str = "") -> bool:
    """True when candidate is a whole-file replacement, not a 1-line snippet."""
    if not candidate or not candidate.strip():
        return False
    if looks_like_helm_values_file(candidate):
        return True
    cand_lines = _nonempty_line_count(candidate)
    if not (base_text or "").strip():
        return cand_lines >= 8
    base_lines = _nonempty_line_count(base_text)
    return cand_lines >= max(8, int(0.5 * base_lines))


def apply_text_patch(base_text: str, original_snippet: str | None, fixed_content: str) -> tuple[str, bool]:
    snippet = strip_unified_diff_fences(original_snippet)
    if not snippet:
        # Explicit full-file replacement mode: accepted only when snippet is intentionally omitted.
        return fixed_content, True

    if snippet in base_text:
        return base_text.replace(snippet, fixed_content, 1), True

    orig_lines = [line.rstrip() for line in snippet.splitlines()]
    curr_lines = base_text.splitlines()
    if orig_lines and len(curr_lines) >= len(orig_lines):
        for idx in range(len(curr_lines) - len(orig_lines) + 1):
            window = [line.rstrip() for line in curr_lines[idx: idx + len(orig_lines)]]
            if window == orig_lines:
                patched_lines = curr_lines[:idx] + fixed_content.splitlines() + curr_lines[idx + len(orig_lines):]
                patched = "\n".join(patched_lines) + ("\n" if base_text.endswith("\n") else "")
                return patched, True

    logger.warning("[VALIDATOR] original_snippet not found in base")
    return base_text, False


def apply_unique_yaml_key_patch(
    base_text: str,
    original_snippet: str | None,
    fixed_content: str,
) -> tuple[str, bool]:
    """Replace a uniquely occurring YAML key line when the LLM guessed the old value wrong.

    Only applies when both sides are a short ``key: value`` snippet and that key appears
    exactly once as a non-comment line in the base file.
    """
    original = strip_unified_diff_fences(original_snippet).strip()
    replacement = (fixed_content or "").strip()
    orig_lines = [line for line in original.splitlines() if line.strip()]
    repl_lines = [line for line in replacement.splitlines() if line.strip()]
    if len(orig_lines) != 1 or len(repl_lines) != 1:
        return base_text, False

    orig_match = _YAML_KEY_RE.match(_COMMENTED_KEY_RE.sub(r"\1", orig_lines[0]))
    repl_match = _YAML_KEY_RE.match(repl_lines[0])
    if not orig_match or not repl_match:
        return base_text, False
    if orig_match.group(2) != repl_match.group(2):
        return base_text, False

    key = orig_match.group(2)
    key_re = re.compile(rf"^(\s*){re.escape(key)}\s*:")
    matches: list[int] = []
    curr_lines = base_text.splitlines()
    for idx, line in enumerate(curr_lines):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if key_re.match(line):
            matches.append(idx)

    if len(matches) != 1:
        return base_text, False

    idx = matches[0]
    indent = key_re.match(curr_lines[idx]).group(1)
    new_line = indent + repl_lines[0].lstrip()
    patched_lines = curr_lines[:idx] + [new_line] + curr_lines[idx + 1:]
    patched = "\n".join(patched_lines) + ("\n" if base_text.endswith("\n") else "")
    return patched, True


def resolve_proposed_text(
    base_text: str,
    original_snippet: str | None,
    content: str,
) -> tuple[str, bool, str]:
    """Decide how to turn a proposal into file text without weakening snippet safety.

    Returns ``(text, patchable, reason)``.
    """
    snippet = strip_unified_diff_fences(original_snippet)
    replacement = content or ""

    if looks_like_complete_file(replacement, base_text):
        return replacement, True, "full_file_replacement"

    if snippet:
        patched, found = apply_text_patch(base_text, snippet, replacement)
        if found:
            return patched, True, "snippet_replaced"
        patched, found = apply_unique_yaml_key_patch(base_text, snippet, replacement)
        if found:
            return patched, True, "unique_key_replaced"
        return base_text, False, "original_snippet_not_found"

    if (base_text or "").strip() and not looks_like_complete_file(replacement, base_text):
        logger.warning("[VALIDATOR] refusing 1-line/snippet content as full-file replacement")
        return base_text, False, "refusing_snippet_as_full_file"

    return replacement, True, "full_file_replacement"


def safe_load_documents(yaml_content: str) -> tuple[list[Any], str | None]:
    try:
        docs = list(yaml.safe_load_all(yaml_content))
    except yaml.YAMLError as exc:
        return [], str(exc)
    return docs, None
