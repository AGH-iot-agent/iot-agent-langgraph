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
from devops_agent.validators.common import resolve_proposed_text
from devops_agent.validators.file_roles import is_helm_values_file

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


def _coerce_proposal_files(proposal: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize LLM output so downstream nodes always see a ``files`` list.

    Models often return a single file dict (``{"path": "...", "content": "..."}``)
    instead of wrapping it in ``files``. Without this, ci_fixer stores an empty
    ``files`` list and sandbox_validator / tests report "produced no files"
    even though path+content are present on the proposal.
    """
    if not isinstance(proposal, dict):
        return {"files": []}

    files = proposal.get("files")
    if isinstance(files, dict):
        proposal["files"] = [files]
        return proposal
    if isinstance(files, list):
        return proposal

    path = proposal.get("path")
    has_body = any(key in proposal for key in ("content", "fixed_snippet", "original_snippet"))
    if isinstance(path, str) and path.strip() and has_body:
        entry: dict[str, Any] = {"path": path}
        for key in ("content", "fixed_snippet", "original_snippet"):
            if key in proposal:
                entry[key] = proposal.pop(key)
        proposal.pop("path", None)
        proposal["files"] = [entry]
        return proposal

    proposal["files"] = []
    return proposal


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

    return _coerce_proposal_files(parsed)


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
    return _coerce_proposal_files(parsed)


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
    "incompatible types for comparison",
    "error calling gt",
    "error calling lt",
    "wrong type for value",
    "cannot compare",
    "nil pointer evaluating",
)
_MANIFEST_WEAK_MARKERS = ("yaml parse", "yaml: unmarshal", "failed to parse")
_HELM_VALUES_PATH_MARKERS = (
    "helm/values-sbx.yaml",
    "helm/values-dev.yaml",
    "values-sbx.yaml",
    "values-dev.yaml",
    "templates/pdb.yaml",
)

_MAVEN_STRONG_MARKERS = (
    "could not resolve dependencies",
    "could not find artifact",
    "non-resolvable parent pom",
)
_MAVEN_WEAK_MARKERS = ("build failure", "maven-dependency-plugin", "dependencyresolutionexception")


_HELM_RUNTIME_MARKERS = (
    "upgrade failed",
    "context deadline exceeded",
    "pending termination",
    "timed out waiting",
    "not ready. status: inprogress",
    "resource deployment/",
)


def _is_helm_deploy_timeout(log_text: str) -> bool:
    lowered = (log_text or "").lower()
    if "helm" not in lowered and "upgrade failed" not in lowered:
        return False
    return any(marker in lowered for marker in _HELM_RUNTIME_MARKERS)


def _is_helm_manifest_issue(log_text: str) -> bool:
    lowered = (log_text or "").lower()
    if any(m in lowered for m in _MANIFEST_STRONG_MARKERS):
        return True
    if "yaml" in lowered and any(m in lowered for m in _MANIFEST_WEAK_MARKERS):
        return True
    if _is_helm_deploy_timeout(log_text):
        return True
    helm_path = any(marker in lowered for marker in _HELM_VALUES_PATH_MARKERS)
    helm_tooling = "helm" in lowered or "pdb.yaml" in lowered
    return helm_path and helm_tooling


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

    parsed = _coerce_proposal_files(parsed)
    if not parsed.get("files") and "root_cause" not in parsed:
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
    combined = " ".join(
        str(item)
        for v in validation_history
        for item in (v.get("errors") or []) + [v.get("output", "")]
    ).lower()
    hint = ""
    if "original_snippet not found" in combined or "not safely patchable" in combined:
        hint = (
            "\nHINT: original_snippet did not match the real file on the branch. "
            "Do NOT send a 1-line diff. Return the COMPLETE corrected file in "
            "files[].content and OMIT original_snippet and fixed_snippet.\n"
        )
    return (
        f"\n\nPREVIOUS ATTEMPTS FAILED ({fix_attempt} of {state.get('max_fix_attempts', 3)} used):\n"
        + anchor + attempts + hint
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
            "OMIT original_snippet and fixed_snippet — this is a full-file replacement. "
            "replicaCount MUST be an unquoted integer (replicaCount: 1), never a quoted "
            "string — Helm `gt .Values.replicaCount 1.0` cannot compare string and float64. "
            "If CI failed on values-sbx.yaml, patch THAT file; do not invent a values-dev.yaml "
            "change unless the PR actually broke values-dev.yaml. "
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
    proposal = _coerce_proposal_files(proposal)
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


def _materialize_proposal_files(
    proposal: dict[str, Any],
    execution_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Turn snippet Helm proposals into full-file ``content`` before validation.

    The sandbox validator and PR nodes both treat empty original_snippet as
    full-file replacement. Materializing here is what used to work for
    scenario_02 (complete values-sbx.yaml) and avoids 1-line diffs that cannot
    be found in the real branch file.
    """
    proposal = _coerce_proposal_files(proposal)
    files = proposal.get("files")
    if not isinstance(files, list):
        return proposal

    materialized: list[dict[str, Any]] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path", ""))
        if not is_helm_values_file(path):
            materialized.append(entry)
            continue

        base_text = _extract_file_content(execution_results, path)
        original_snippet = entry.get("original_snippet", "") or ""
        replacement = entry.get("content") or entry.get("fixed_snippet") or ""
        if not replacement:
            materialized.append(entry)
            continue

        text, patchable, reason = resolve_proposed_text(base_text, original_snippet, replacement)
        if patchable and text:
            logger.info(
                "[CI_FIXER] Materialized %s as full-file content (reason=%s, lines=%d)",
                path, reason, text.count("\n") + 1,
            )
            materialized.append({
                **entry,
                "content": text,
                "original_snippet": "",
                "fixed_snippet": "",
            })
        else:
            logger.warning(
                "[CI_FIXER] Could not materialize %s (reason=%s) — leaving proposal as-is",
                path, reason,
            )
            materialized.append(entry)

    proposal["files"] = materialized
    return proposal


def _service_under_change(context: dict[str, Any]) -> str:
    """Service the failing PR belongs to, used to scope shared-namespace cluster evidence."""
    repo = str(context.get("repo_full_name") or "")
    return repo.rsplit("/", 1)[-1].strip().lower()


def _object_name(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("name", "pod", "pod_name"):
            value = item.get(key)
            if value:
                return str(value)
        involved = item.get("involvedObject")
        if isinstance(involved, dict) and involved.get("name"):
            return str(involved["name"])
        metadata = item.get("metadata")
        if isinstance(metadata, dict) and metadata.get("name"):
            return str(metadata["name"])
    return ""


def _belongs_to_service(item: Any, service: str) -> bool:
    name = _object_name(item).lower()
    if not name:
        return True
    return name == service or name.startswith(f"{service}-")


def _scope_to_service(payload: Any, service: str) -> Any:
    """Drop other services' pods/events from a shared namespace so they cannot be diagnosed."""
    if not service or not isinstance(payload, dict):
        return payload
    scoped = dict(payload)
    for key in ("pods", "events", "items"):
        values = scoped.get(key)
        if not isinstance(values, list):
            continue
        kept = [item for item in values if _belongs_to_service(item, service)]
        if len(kept) != len(values):
            scoped[f"{key}_omitted_other_services"] = len(values) - len(kept)
        scoped[key] = kept
    return scoped


def _cluster_diagnostic_block(
    execution_results: list[dict[str, Any]],
    context: dict[str, Any],
) -> str:
    """Keep OOM/CrashLoop facts out of the truncated CI-log dump."""
    chunks: list[str] = []
    service = _service_under_change(context)
    oom = context.get("oom_evidence") or []
    if oom:
        chunks.append(
            "CLUSTER OOM EVIDENCE (captured when CI failed; Helm logs usually omit this):\n"
            + json.dumps(oom, indent=2, default=str)
        )
    snapshot = context.get("cluster_snapshot") or {}
    if snapshot.get("pods") or snapshot.get("events"):
        compact = {
            "namespace": snapshot.get("namespace"),
            "pods": [
                pod for pod in (snapshot.get("pods") or [])
                if _belongs_to_service(pod, service)
            ],
            "oom_events": [
                evt for evt in (snapshot.get("events") or [])
                if "oom" in str(evt).lower() or evt.get("reason") in {"OOMKilled", "OOMKilling"}
            ],
        }
        chunks.append("CLUSTER SNAPSHOT AT CI FAILURE:\n" + json.dumps(compact, indent=2, default=str))

    for result in execution_results:
        if result.get("tool") != "kubernetes":
            continue
        payload = result.get("result") or {}
        action = result.get("action")
        if action in {"get_pods", "get_events", "get_pod", "describe_pod", "get_pod_logs"}:
            scoped = _scope_to_service(payload, service)
            chunks.append(
                f"KUBERNETES {action}:\n" + json.dumps(scoped, indent=2, default=str)[:4000]
            )
    if not chunks:
        return ""
    scope_note = (
        f"All cluster data below is scoped to the service changed by this PR (`{service}`); "
        "unrelated workloads in the shared namespace were removed. Do not diagnose another service.\n"
        if service
        else ""
    )
    return (
        "Helm `UPGRADE FAILED` / `context deadline exceeded` is a timeout waiting for pods. "
        "Diagnose from the cluster data below (OOMKilled, exit 137, CrashLoopBackOff, probes). "
        "Do not invent a Spring Boot memory recipe unless the workload is actually Java.\n"
        + scope_note
        + "\n"
        + "\n\n".join(chunks)
        + "\n\n"
    )


def ci_fixer_node(state: AgentState) -> AgentState:
    """Analyze a CI failure and produce an LLM-driven fix proposal."""
    context: dict[str, Any] = state.get("context", {})
    repo: str = context.get("repo_full_name", "")
    branch: str = context.get("branch", "main")
    run_url: str = context.get("run_url", "")

    execution_results: list[dict[str, Any]] = state.get("execution_results", [])
    log_text = _collect_log_text(execution_results)
    if context.get("oom_evidence") and not _is_maven_pom_issue(log_text):
        issue_type = ISSUE_TYPE_HELM_MANIFEST
    else:
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

    cluster_block = _cluster_diagnostic_block(execution_results, context)

    schema, rules = _output_schema(issue_type, real_paths)
    known_paths = _KNOWN_PATHS_BY_ISSUE_TYPE.get(issue_type, ())
    file_context = _known_file_context(execution_results, known_paths) if known_paths else ""
    feedback = _build_feedback(state, fix_attempt, validation_history)

    event_kind = state.get("event_kind", "default")
    trace_id = context.get("trace_id") or context.get("run_id")
    system_prompt = _build_system_prompt(event_kind, trace_id=str(trace_id) if trace_id else None)

    user_prompt = (
        f"Repo: {repo} | Branch: {branch} | Run: {run_url}\n\n"
        f"{cluster_block}"
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
    proposal = _materialize_proposal_files(proposal, execution_results)

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