from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from devops_agent.llm_utils import invoke_with_retry as _invoke_with_retry
from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.state import AgentState
from devops_agent.prompts import _build_system_prompt

logger = logging.getLogger(__name__)

_llm: ChatOpenAI | None = None
_adapter = MCPAdapter()

_DEFAULT_LLM_MODEL = os.getenv("DEVOPS_AGENT_MODEL", "gpt-4o-mini")

_VALUES_SBX_PATH = "Helm/values-sbx.yaml"
_VALUES_DEV_PATH = "Helm/values-dev.yaml"
_MANIFEST_VALUES_PATHS = (_VALUES_SBX_PATH, _VALUES_DEV_PATH)

_POM_PATH = "pom.xml"
_MAVEN_CONFIG_PATHS = (_POM_PATH,)

ISSUE_TYPE_HELM_MANIFEST = "helm_manifest"
ISSUE_TYPE_MAVEN_POM = "maven_pom"
ISSUE_TYPE_GENERIC = "generic"

_FILE_PATH_RE = re.compile(r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,10}")
_ALLOWED_EXTENSIONS = {
    "yaml", "yml", "json", "toml", "ini", "properties", "java", "kt", "py",
    "js", "ts", "tsx", "go", "xml", "sql", "sh", "env",
}
_IGNORE_PATH_PREFIXES = ("/", "http://", "https://", "node_modules/")
_IGNORE_SEGMENTS = ("::", " ")
_RAW_BLOCK_MAX_CHARS = 12000
_MAX_EXTRA_LOG_HINT_FILES = 10
_MAX_ALLOWED_PATHS_IN_PROMPT = 20


def _get_llm() -> ChatOpenAI:
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(model=_DEFAULT_LLM_MODEL, temperature=0, max_tokens=10000)
    return _llm


def _score_json_candidate(raw_json: str) -> int:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return -1

    if not isinstance(parsed, dict):
        return 0

    score = 0
    if "files" in parsed and isinstance(parsed.get("files"), list):
        score += 100
    if "root_cause" in parsed:
        score += 40
    if "description" in parsed:
        score += 20
    if "error_message" in parsed:
        score += 20
    score += min(len(raw_json) // 500, 20)
    return score


def _scan_best_json_object(src: str) -> str | None:
    decoder = json.JSONDecoder()
    best_candidate: str | None = None
    best_score = -1

    for i, ch in enumerate(src):
        if ch != "{":
            continue
        try:
            _, end_idx = decoder.raw_decode(src[i:])
            candidate = src[i : i + end_idx]
            score = _score_json_candidate(candidate)
            if score > best_score:
                best_score = score
                best_candidate = candidate
        except json.JSONDecodeError:
            continue

    return best_candidate


def _extract_json_object(text: str) -> str | None:
    fenced = re.search(r"```(?:json)?[ \t]*\r?\n([\s\S]+?)\r?\n[ \t]*```", text)
    if fenced:
        result = _scan_best_json_object(fenced.group(1))
        if result:
            return result

    return _scan_best_json_object(text)


def _parse_proposal_json(raw: str) -> dict[str, Any] | None:
    json_str = _extract_json_object(raw)
    if json_str is None:
        return None

    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    return parsed


def _retry_llm_for_json(llm, bad_output: str, messages: list) -> dict[str, Any]:
    repair = messages + [HumanMessage(content=(
        "Your previous response could not be parsed as JSON. "
        "Return ONLY the JSON object — no prose, no code fences.\n\n"
        f"Broken output (first 500 chars):\n{bad_output[:500]}"
    ))]
    resp = _invoke_with_retry(llm, repair)
    parsed = _parse_proposal_json(resp.content)
    if parsed is None:
        raise ValueError("Still no valid JSON after retry")
    return parsed


def _normalize_path(path: str) -> str:
    p = (path or "").strip().strip('"\'`')
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if p.startswith("a/") or p.startswith("b/"):
        p = p[2:]
    return p


def _collect_log_text(execution_results: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for r in execution_results:
        payload = r.get("result", {})
        if isinstance(payload, dict):
            for key in ("logs", "message", "output"):
                v = payload.get(key)
                if isinstance(v, str) and v:
                    chunks.append(v)
    return "\n".join(chunks)


_MANIFEST_STRONG_MARKERS = (
    "did not find expected key",
    "mapping values are not allowed",
    "error converting yaml to json",
)
_MANIFEST_WEAK_MARKERS = ("yaml parse", "yaml: unmarshal", "failed to parse")

_MAVEN_STRONG_MARKERS = (
    "could not resolve dependencies",
    "could not find artifact",
    "non-resolvable parent pom",
)
_MAVEN_WEAK_MARKERS = ("build failure", "maven-dependency-plugin", "dependencyresolutionexception")


def _is_helm_manifest_issue(log_text: str) -> bool:
    lowered = (log_text or "").lower()
    if any(m in lowered for m in _MANIFEST_STRONG_MARKERS):
        return True
    return "yaml" in lowered and any(m in lowered for m in _MANIFEST_WEAK_MARKERS)


def _is_maven_pom_issue(log_text: str) -> bool:
    lowered = (log_text or "").lower()
    if any(m in lowered for m in _MAVEN_STRONG_MARKERS):
        return True
    return "maven" in lowered and any(m in lowered for m in _MAVEN_WEAK_MARKERS)


def _classify_issue(log_text: str) -> str:
    """Classify a CI failure into a known file-target bucket, or 'generic'
    if it doesn't match a known pattern (falls back to log-hint scraping)."""
    if _is_helm_manifest_issue(log_text):
        return ISSUE_TYPE_HELM_MANIFEST
    if _is_maven_pom_issue(log_text):
        return ISSUE_TYPE_MAVEN_POM
    return ISSUE_TYPE_GENERIC


_KNOWN_PATHS_BY_ISSUE_TYPE: dict[str, tuple[str, ...]] = {
    ISSUE_TYPE_HELM_MANIFEST: _MANIFEST_VALUES_PATHS,
    ISSUE_TYPE_MAVEN_POM: _MAVEN_CONFIG_PATHS,
}


def _extract_file_content(execution_results: list[dict[str, Any]], path: str) -> str:
    for r in execution_results:
        if r.get("action") == "get_file_content" and r.get("args", {}).get("path") == path:
            payload = r.get("result", {})
            if isinstance(payload, dict) and payload.get("status") == "ok":
                return str(payload.get("content", ""))
    return ""


def _fetch_known_paths(
    execution_results: list[dict[str, Any]],
    repo: str,
    branch: str,
    known_paths: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Ensure each path in known_paths has a successful get_file_content
    result in execution_results, fetching any that are missing.
    """
    already_have = {
        r["args"]["path"]
        for r in execution_results
        if r.get("action") == "get_file_content"
        and r.get("result", {}).get("status") == "ok"
        and r.get("args", {}).get("path") in known_paths
    }
    extra: list[dict[str, Any]] = []
    for path in known_paths:
        if path in already_have:
            continue
        result = _adapter.run(
            "github", "get_file_content",
            {"repo": repo, "path": path, "ref": branch},
            dry_run=False,
        )
        if result.get("status") == "ok":
            extra.append({
                "tool": "github", "action": "get_file_content",
                "args": {"repo": repo, "path": path, "ref": branch},
                "result": result,
            })
        else:
            logger.warning(
                "[CI_FIXER] Could not fetch known path %s from %s@%s: %s",
                path, repo, branch, result.get("message"),
            )
    if extra:
        logger.info("[CI_FIXER] Fetched %d known-path file(s): %s", len(extra), known_paths)
    return execution_results + extra


def _prepare_context(
    execution_results: list[dict[str, Any]],
    repo: str,
    branch: str,
    issue_type: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    real_paths: list[str] = [
        r["args"]["path"]
        for r in execution_results
        if r.get("action") == "get_file_content"
        and r.get("result", {}).get("status") == "ok"
        and r.get("args", {}).get("path")
    ]

    known_paths = _KNOWN_PATHS_BY_ISSUE_TYPE.get(issue_type)
    if known_paths:
        execution_results = _fetch_known_paths(execution_results, repo, branch, known_paths)
        real_paths = list(dict.fromkeys(real_paths + list(known_paths)))
        return execution_results, [p for p in real_paths if p in set(known_paths)]

    log_text = _collect_log_text(execution_results)
    candidates: list[str] = []
    for m in _FILE_PATH_RE.finditer(log_text):
        p = m.group(0).strip("'\"`[](){}:,;")
        ext = p.rsplit(".", 1)[-1].lower() if "." in p else ""
        if (
            ext in _ALLOWED_EXTENSIONS
            and not any(p.startswith(x) for x in _IGNORE_PATH_PREFIXES)
            and not any(s in p for s in _IGNORE_SEGMENTS)
            and p.count("/") <= 8
        ):
            candidates.append(p)

    known = set(real_paths)
    extra: list[dict[str, Any]] = []
    for p in list(dict.fromkeys(candidates))[:_MAX_EXTRA_LOG_HINT_FILES]:
        if p in known:
            continue
        result = _adapter.run(
            "github", "get_file_content",
            {"repo": repo, "path": p, "ref": branch},
            dry_run=False,
        )
        if result.get("status") == "ok":
            extra.append({
                "tool": "github", "action": "get_file_content",
                "args": {"repo": repo, "path": p, "ref": branch},
                "result": result,
            })
            real_paths.append(p)
            known.add(p)

    if extra:
        logger.info("[CI_FIXER] Fetched %d additional files from CI logs", len(extra))
    return execution_results + extra, real_paths


def _call_llm(messages: list) -> dict[str, Any]:
    llm = _get_llm()
    response = _invoke_with_retry(llm, messages)
    raw = response.content
    logger.info("[CI_FIXER] LLM response chars=%d", len(raw or ""))
    parsed = _parse_proposal_json(raw)
    if parsed is None:
        raise ValueError("No JSON object found in LLM output")

    if "files" not in parsed and "root_cause" not in parsed:
        logger.warning("[CI_FIXER] Parsed JSON missing expected keys — retrying")
        return _retry_llm_for_json(llm, raw, messages)

    return parsed


def _build_feedback(
    state: AgentState,
    fix_attempt: int,
    validation_history: list[dict[str, Any]],
) -> str:
    if fix_attempt <= 0 or not validation_history:
        return ""
    prev_rc = str((state.get("ci_fix_proposal") or {}).get("root_cause", ""))
    anchor = f"\nYOUR PREVIOUS ROOT CAUSE: {prev_rc}\nKeep fixing THAT problem.\n" if prev_rc else ""
    attempts = "\n".join(
        f"--- Attempt {i + 1} ---\n{chr(10).join(v.get('errors', []))}\n{v.get('output', '')}"
        for i, v in enumerate(validation_history)
    )
    return (
        f"\n\nPREVIOUS ATTEMPTS FAILED ({fix_attempt} of {state.get('max_fix_attempts', 3)} used):\n"
        + anchor + attempts
        + "\n\nFocus on the original root cause. Do NOT repeat the same change.\n"
    )


def _known_file_context(execution_results: list[dict[str, Any]], paths: tuple[str, ...]) -> str:
    sections = []
    for p in paths:
        c = _extract_file_content(execution_results, p)
        if c:
            sections.append(f"\n--- FILE: {p} ---\n{c}\n--- END ---\n")
    return "\n".join(sections)


def _output_schema(issue_type: str, real_paths: list[str]) -> tuple[str, str]:
    if issue_type == ISSUE_TYPE_HELM_MANIFEST:
        schema = (
            '{"root_cause":"one line","error_message":"exact error","description":"what failed and why",'
            f'"files":[{{"path":"{_VALUES_SBX_PATH}","content":"COMPLETE corrected file content"}}]}}'
        )
        rules = (
            "Output the COMPLETE corrected Helm values file in the 'content' field. "
            "Base it on the exact FILE content shown above — change only what's needed to fix "
            "the reported error, keep everything else byte-for-byte identical. "
            f"Allowed paths: {', '.join(_MANIFEST_VALUES_PATHS)}\n"
        )
    elif issue_type == ISSUE_TYPE_MAVEN_POM:
        schema = (
            '{"root_cause":"one line","error_message":"exact error","description":"what failed and why",'
            f'"files":[{{"path":"{_POM_PATH}","content":"COMPLETE corrected pom.xml content"}}]}}'
        )
        rules = (
            "Output the COMPLETE corrected pom.xml in the 'content' field. "
            "Base it on the exact FILE content shown above — change ONLY the offending dependency "
            "coordinate/version, keep everything else (including formatting) byte-for-byte identical. "
            "Do NOT invent dependencies, sections, or files that are not present in the FILE content shown. "
            f"Allowed paths: {', '.join(_MAVEN_CONFIG_PATHS)}\n"
        )
    else:
        schema = (
            '{"root_cause":"one line","error_message":"exact error","description":"what failed and why",'
            '"files":[{"path":"file/path","original_snippet":"minimal broken lines","fixed_snippet":"replacement lines"}]}'
        )
        rules = f"Allowed paths: {', '.join(real_paths[:_MAX_ALLOWED_PATHS_IN_PROMPT])}\n" if real_paths else ""
    return schema, rules


def _filter_files(
    proposal: dict[str, Any],
    real_paths: list[str],
    issue_type: str,
) -> dict[str, Any]:
    extra_allowed = set(_KNOWN_PATHS_BY_ISSUE_TYPE.get(issue_type, ()))
    allowed = {_normalize_path(p) for p in (set(real_paths) | extra_allowed)}
    files = proposal.get("files")
    if not isinstance(files, list):
        proposal["files"] = []
        return proposal

    normalized_files: list[dict[str, Any]] = []
    dropped: list[str] = []

    for entry in files:
        if not isinstance(entry, dict):
            continue
        original_path = str(entry.get("path", ""))
        normalized_path = _normalize_path(original_path)
        if normalized_path in allowed:
            normalized_entry = dict(entry)
            normalized_entry["path"] = normalized_path
            normalized_files.append(normalized_entry)
        else:
            dropped.append(original_path)

    if dropped:
        logger.warning("[CI_FIXER] Dropped disallowed paths: %s", dropped)

    if files and not normalized_files:
        logger.warning(
            "[CI_FIXER] All proposed files were filtered out. allowed=%s sample_input_paths=%s",
            sorted(allowed),
            [str(entry.get("path", "")) for entry in files[:5] if isinstance(entry, dict)],
        )

    proposal["files"] = normalized_files
    return proposal


def ci_fixer_node(state: AgentState) -> AgentState:
    """Analyze a CI failure and produce an LLM-driven fix proposal."""
    context: dict[str, Any] = state.get("context", {})
    repo: str = context.get("repo_full_name", "")
    branch: str = context.get("branch", "main")
    run_url: str = context.get("run_url", "")

    execution_results: list[dict[str, Any]] = state.get("execution_results", [])
    log_text = _collect_log_text(execution_results)
    issue_type = _classify_issue(log_text)

    execution_results, real_paths = _prepare_context(execution_results, repo, branch, issue_type)

    fix_attempt: int = state.get("fix_attempt", 0)
    validation_history: list[dict] = state.get("validation_history") or []

    logger.info(
        "[CI_FIXER] repo=%s branch=%s fix_attempt=%d issue_type=%s paths=%s",
        repo, branch, fix_attempt, issue_type, real_paths,
    )

    raw_block = json.dumps(execution_results, indent=2, default=str)
    if len(raw_block) > _RAW_BLOCK_MAX_CHARS:
        raw_block = raw_block[:_RAW_BLOCK_MAX_CHARS] + "\n[...truncated...]"

    schema, rules = _output_schema(issue_type, real_paths)
    known_paths = _KNOWN_PATHS_BY_ISSUE_TYPE.get(issue_type, ())
    file_context = _known_file_context(execution_results, known_paths) if known_paths else ""
    feedback = _build_feedback(state, fix_attempt, validation_history)

    event_kind = state.get("event_kind", "default")
    trace_id = context.get("trace_id") or context.get("run_id")
    system_prompt = _build_system_prompt(event_kind, trace_id=str(trace_id) if trace_id else None)

    user_prompt = (
        f"Repo: {repo} | Branch: {branch} | Run: {run_url}\n\n"
        f"Logs/data:\n{raw_block}\n"
        f"{file_context}\n"
        f"{feedback}\n"
        f"Output ONLY this JSON (no prose):\n{schema}\n"
        f"{rules}"
        f'If cannot determine: {{"root_cause":"unknown","error_message":"","description":"","files":[]}}'
    )

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    try:
        proposal = _call_llm(messages)
    except Exception as exc:
        logger.exception("[CI_FIXER] LLM call failed")
        proposal = {"root_cause": f"LLM error: {exc}", "files": []}

    logger.debug(
        "[CI_FIXER] Raw proposal before filtering:\n%s",
        json.dumps(proposal, indent=2, default=str)
    )

    proposal = _filter_files(proposal, real_paths, issue_type)

    logger.debug(
        "[CI_FIXER] Filtered proposal:\n%s",
        json.dumps(proposal, indent=2, default=str)
    )

    logger.info(
        "[CI_FIXER] root_cause=%s files=%d paths=%s",
        proposal.get("root_cause", "?"),
        len(proposal.get("files", [])),
        [e.get("path") for e in proposal.get("files", [])],
    )

    return {**state, "ci_fix_proposal": proposal, "fix_attempt": fix_attempt + 1}