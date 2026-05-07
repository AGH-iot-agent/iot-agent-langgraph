from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.state import AgentState
from devops_agent.prompts import _build_system_prompt

logger = logging.getLogger(__name__)

_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=3000)
_adapter = MCPAdapter()


def _invoke_with_retry(llm, messages, max_retries: int = 4):
    """Invoke LLM with exponential backoff on 429 RateLimitError."""
    from openai import RateLimitError
    delay = 5.0
    for attempt in range(max_retries):
        try:
            return llm.invoke(messages)
        except RateLimitError as exc:
            if attempt == max_retries - 1:
                raise
            logger.warning("[CI_FIXER] Rate limit hit (attempt %d/%d), retrying in %.1fs: %s",
                           attempt + 1, max_retries, delay, exc)
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
    return llm.invoke(messages)

def _load_prompt(name: str) -> str:
    p = Path(__file__).parent.parent / "prompts" / f"{name}.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def ci_fixer_node(state: AgentState) -> AgentState:
    """
        Analyze a CI failure and produce a fix proposal.

        Reads job logs, the failing workflow YAML, and Helm values from GitHub,
        then asks the LLM to identify root cause and propose concrete file changes.
        The result is stored in state['ci_fix_proposal'].
    """
    context: dict[str, Any] = state.get("context", {})
    repo: str = context.get("repo_full_name", "")
    branch: str = context.get("branch", "main")
    run_url: str = context.get("run_url", "")

    execution_results: list[dict[str, Any]] = state.get("execution_results", [])

    real_paths: list[str] = [
        r["args"]["path"]
        for r in execution_results
        if r.get("action") == "get_file_content"
        and r.get("result", {}).get("status") == "ok"
        and r.get("args", {}).get("path")
    ]
    logger.debug("[CI_FIXER] real_paths available for fix: %s", real_paths)

    raw_block = json.dumps(execution_results, indent=2, default=str)

    MAX_RAW = 4000
    if len(raw_block) > MAX_RAW:
        raw_block = raw_block[:MAX_RAW] + "\n[...truncated...]"

    event_kind = state.get("event_kind", "default")
    system_prompt = _build_system_prompt(event_kind)

    if real_paths:
        path_constraint = (
            f"\nYou MUST use ONLY these exact file paths in the 'files' array: {json.dumps(real_paths)}"
            "\nDO NOT invent, guess, or use placeholder paths."
        )
    else:
        path_constraint = (
            "\nNo file content was successfully fetched. "
            "You MUST return: {\"root_cause\":\"unknown\",\"error_message\":\"\",\"description\":\"\",\"files\":[]}"
        )

    user_prompt = (
        f"Repo: {repo} | Branch: {branch} | Run: {run_url}\n\n"
        f"Logs/data:\n{raw_block}\n\n"
        f"Output ONLY this JSON (no prose):\n"
        '{"root_cause":"one line",'
        '"error_message":"exact error line(s) from logs",'
        '"description":"2-3 sentences: what failed and why",'
        '"files":[{"path":"path","original_snippet":"broken lines","fixed_snippet":"fixed lines"}]}\n'
        f"If cannot determine: {{\"root_cause\":\"unknown\",\"error_message\":\"\",\"description\":\"\",\"files\":[]}}"
        f"{path_constraint}"
    )

    messages = [    
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    try:
        response = _invoke_with_retry(_llm, messages)
        raw = response.content
        logger.debug("[CI_FIXER] LLM raw response: %s", raw[:500])

        import re
        fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
        if fenced:
            json_str = fenced.group(1)
        else:
            start = raw.find("{")
            end = raw.rfind("}")
            json_str = raw[start:end + 1] if start != -1 and end != -1 else raw.strip()
        proposal = json.loads(json_str)
    except Exception as exc:
        logger.exception("[CI_FIXER] Failed to parse LLM response")
        proposal = {"root_cause": f"LLM error: {exc}", "files": []}

    logger.info(
        "[CI_FIXER] root_cause=%s files=%d",
        proposal.get("root_cause", "?"),
        len(proposal.get("files", [])),
    )

    return {
        **state,
        "ci_fix_proposal": proposal,
    }
