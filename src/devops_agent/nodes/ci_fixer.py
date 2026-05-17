from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
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

_FILE_PATH_RE = re.compile(r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,10}")
_ALLOWED_EXTENSIONS = {
    "yaml", "yml", "json", "toml", "ini", "properties", "java", "kt", "py",
    "js", "ts", "tsx", "go", "xml", "sql", "sh", "env",
}
_IGNORE_PATH_PREFIXES = ("/", "http://", "https://", "node_modules/")
_IGNORE_SEGMENTS = ("::", " ")


def _get_llm() -> ChatOpenAI:
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(model=_DEFAULT_LLM_MODEL, temperature=0, max_tokens=8000)
    return _llm


def _load_prompt(name: str) -> str:
    p = Path(__file__).parent.parent / "prompts" / f"{name}.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def _extract_json_object(text: str) -> str | None:
    decoder = json.JSONDecoder()

    def _scan(src: str) -> str | None:
        for i, ch in enumerate(src):
            if ch != "{":
                continue
            try:
                _, end_idx = decoder.raw_decode(src[i:])
                return src[i : i + end_idx]
            except json.JSONDecodeError:
                continue
        return None

    # Capture ALL content between fence markers, then locate the balanced JSON
    # object via raw_decode.  The old non-greedy \{[\s\S]*?\} regex stopped at
    # the FIRST } found, which was the closing brace of the inner file-entry
    # object {"path":"...","content":"..."}, not the outer root object.
    fenced = re.search(r"```(?:json)?[ \t]*\r?\n([\s\S]+?)\r?\n[ \t]*```", text)
    if fenced:
        result = _scan(fenced.group(1))
        if result:
            return result

    return _scan(text)


def _retry_llm_for_json(llm, bad_output: str, messages: list) -> dict[str, Any]:
    repair = messages + [HumanMessage(content=(
        "Your previous response could not be parsed as JSON. "
        "Return ONLY the JSON object — no prose, no code fences.\n\n"
        f"Broken output (first 500 chars):\n{bad_output[:500]}"
    ))]
    resp = _invoke_with_retry(llm, repair)
    json_str = _extract_json_object(resp.content)
    if json_str is None:
        raise ValueError("Still no valid JSON after retry")
    return json.loads(json_str)


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


def _is_manifest_issue(log_text: str) -> bool:
    lowered = (log_text or "").lower()
    return any(t in lowered for t in (
        "helm", "kubernetes", "kubectl", "manifest", "values-sbx.yaml",
        "values-dev.yaml", "did not find expected key", "mapping values are not allowed",
        "yaml parse", "yaml: unmarshal", "failed to parse",
    ))


def _extract_file_content(execution_results: list[dict[str, Any]], path: str) -> str:
    for r in execution_results:
        if r.get("action") == "get_file_content" and r.get("args", {}).get("path") == path:
            payload = r.get("result", {})
            if isinstance(payload, dict) and payload.get("status") == "ok":
                return str(payload.get("content", ""))
    return ""


def _prepare_context(
    execution_results: list[dict[str, Any]],
    repo: str,
    branch: str,
    is_manifest: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    real_paths: list[str] = [
        r["args"]["path"]
        for r in execution_results
        if r.get("action") == "get_file_content"
        and r.get("result", {}).get("status") == "ok"
        and r.get("args", {}).get("path")
    ]

    if is_manifest:
        return execution_results, [p for p in real_paths if p in set(_MANIFEST_VALUES_PATHS)]

    # Fetch additional files hinted at in CI log text
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
    for p in list(dict.fromkeys(candidates))[:10]:
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
    json_str = _extract_json_object(raw)
    if json_str is None:
        raise ValueError("No JSON object found in LLM output")
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as exc:
        logger.warning("[CI_FIXER] JSON parse failed (%s) — retrying", exc)
        return _retry_llm_for_json(llm, raw, messages)


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


def _manifest_file_context(execution_results: list[dict[str, Any]]) -> str:
    sections = []
    for p in _MANIFEST_VALUES_PATHS:
        c = _extract_file_content(execution_results, p)
        if c:
            sections.append(f"\n--- FILE: {p} ---\n{c}\n--- END ---\n")
    return "\n".join(sections)


def _output_schema(is_manifest: bool, real_paths: list[str]) -> tuple[str, str]:
    if is_manifest:
        schema = (
            '{"root_cause":"one line","error_message":"exact error","description":"what failed and why",'
            f'"files":[{{"path":"{_VALUES_SBX_PATH}","content":"COMPLETE corrected file content"}}]}}'
        )
        rules = (
            "Output the COMPLETE corrected Helm values file in the 'content' field. "
            f"Allowed paths: {', '.join(_MANIFEST_VALUES_PATHS)}\n"
        )
    else:
        schema = (
            '{"root_cause":"one line","error_message":"exact error","description":"what failed and why",'
            '"files":[{"path":"file/path","original_snippet":"minimal broken lines","fixed_snippet":"replacement lines"}]}'
        )
        rules = f"Allowed paths: {', '.join(real_paths[:20])}\n" if real_paths else ""
    return schema, rules


def _filter_files(
    proposal: dict[str, Any],
    real_paths: list[str],
    is_manifest: bool,
) -> dict[str, Any]:
    allowed = set(real_paths) | (set(_MANIFEST_VALUES_PATHS) if is_manifest else set())
    files = proposal.get("files") or []
    filtered = [e for e in files if isinstance(e, dict) and e.get("path") in allowed]
    dropped = [e.get("path") for e in files if isinstance(e, dict) and e.get("path") not in allowed]
    if dropped:
        logger.warning("[CI_FIXER] Dropped disallowed paths: %s", dropped)
    proposal["files"] = filtered
    return proposal


def ci_fixer_node(state: AgentState) -> AgentState:
    """Analyze a CI failure and produce an LLM-driven fix proposal."""
    context: dict[str, Any] = state.get("context", {})
    repo: str = context.get("repo_full_name", "")
    branch: str = context.get("branch", "main")
    run_url: str = context.get("run_url", "")

    execution_results: list[dict[str, Any]] = state.get("execution_results", [])
    log_text = _collect_log_text(execution_results)
    is_manifest = _is_manifest_issue(log_text)

    execution_results, real_paths = _prepare_context(execution_results, repo, branch, is_manifest)

    fix_attempt: int = state.get("fix_attempt", 0)
    validation_history: list[dict] = state.get("validation_history") or []

    logger.info(
        "[CI_FIXER] repo=%s branch=%s fix_attempt=%d manifest=%s paths=%s",
        repo, branch, fix_attempt, is_manifest, real_paths,
    )

    raw_block = json.dumps(execution_results, indent=2, default=str)
    if len(raw_block) > 12000:
        raw_block = raw_block[:12000] + "\n[...truncated...]"

    schema, rules = _output_schema(is_manifest, real_paths)
    manifest_context = _manifest_file_context(execution_results) if is_manifest else ""
    feedback = _build_feedback(state, fix_attempt, validation_history)

    event_kind = state.get("event_kind", "default")
    trace_id = context.get("trace_id") or context.get("run_id")
    system_prompt = _build_system_prompt(event_kind, trace_id=str(trace_id) if trace_id else None)

    user_prompt = (
        f"Repo: {repo} | Branch: {branch} | Run: {run_url}\n\n"
        f"Logs/data:\n{raw_block}\n"
        f"{manifest_context}\n"
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

    proposal = _filter_files(proposal, real_paths, is_manifest)

    logger.info(
        "[CI_FIXER] root_cause=%s files=%d paths=%s",
        proposal.get("root_cause", "?"),
        len(proposal.get("files", [])),
        [e.get("path") for e in proposal.get("files", [])],
    )

    return {**state, "ci_fix_proposal": proposal, "fix_attempt": fix_attempt + 1}
