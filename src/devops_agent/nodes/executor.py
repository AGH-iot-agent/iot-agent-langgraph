from __future__ import annotations

import json
import logging
import time
from typing import Any
from pathlib import Path

from devops_agent.graph_tools import ALL_TOOLS
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.state import AgentState

from devops_agent.prompts import _build_system_prompt


logger = logging.getLogger(__name__)

_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=2000)
_llm_with_tools = _llm.bind_tools(ALL_TOOLS)
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
            logger.warning("[EXECUTOR] Rate limit hit (attempt %d/%d), retrying in %.1fs: %s",
                           attempt + 1, max_retries, delay, exc)
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
    return llm.invoke(messages)  # unreachable, satisfies type checkers

def load_prompt(name: str) -> str:
    return Path(f"../prompts/{name}.md").read_text(encoding="utf-8").strip()

def executor_node(state: AgentState) -> AgentState:
    plan: list[dict[str, Any]] = state.get("plan", [])
    dry_run: bool = state.get("dry_run", True)
    event_kind = state.get("event_kind", "default")

    if not plan:
        return {
            **state,
            "execution_results": [],
            "execution_summary": "No steps in plan — nothing executed.",
        }

    raw_results: list[dict[str, Any]] = []
    for i, step in enumerate(plan):
        tool    = step.get("tool", "unknown")
        action  = step.get("action", "unknown")
        args    = step.get("args", {})

        result = _adapter.run(service=tool, tool=action, params=args, dry_run=dry_run)

        if result.get("status") == "error" and "Unknown action" in result.get("message", ""):
            continue

        raw_results.append({
            "step":   i + 1,
            "tool":   tool,
            "action": action,
            "args":   args,
            "result": result,
        })

    meaningful_results = [
        r for r in raw_results
        if not (
            r.get("result", {}).get("status") == "error"
            and "Unknown tool" in r.get("result", {}).get("message", "")
        )
    ]

    if event_kind == "github_issue":
        meaningful_results = [
            r for r in meaningful_results
            if r.get("tool") != "kubernetes"
        ]

    results_block = json.dumps(meaningful_results, indent=2, default=str)

    MAX_RESULTS_CHARS = 8000
    if len(results_block) > MAX_RESULTS_CHARS:
        results_block = results_block[-MAX_RESULTS_CHARS:]
        results_block = "[... truncated to last 8000 chars ...]\n" + results_block

    issue_body = state.get('issue_body', '')
    MAX_BODY_CHARS = 2000
    if len(issue_body) > MAX_BODY_CHARS:
        issue_body = issue_body[:MAX_BODY_CHARS] + "\n[... truncated ...]"

    system_prompt = _build_system_prompt(event_kind)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=(
            f"## Event\n"
            f"**Title:** {state.get('issue_title', '')}\n\n"
            f"**Body:**\n{issue_body}\n\n"
            f"---\n"
            f"## Tool Results\n"
            f"{results_block}\n\n"
            f"---\n"
            f"Write a GitHub comment that DIRECTLY SOLVES the issue above.\n"
            f"DO NOT repeat or paraphrase the issue description.\n"
            f"Your response MUST follow this exact structure:\n\n"
            f"## \U0001f50d Root Cause\n"
            f"(one precise sentence identifying the exact cause from tool results)\n\n"
            f"## \U0001f6e0\ufe0f Proposed Fix\n"
            f"(exact YAML snippets, kubectl commands, or code changes — be specific)\n\n"
            f"## \u2705 Steps to Resolve\n"
            f"(numbered actionable steps a developer can follow right now)\n\n"
            f"## \u26a0\ufe0f Risk / Side Effects\n"
            f"(what could go wrong, what to verify after applying the fix)"
        ))
    ]

    ai_response: AIMessage = _invoke_with_retry(_llm_with_tools, messages)

    tool_call_rounds = 0
    while getattr(ai_response, "tool_calls", None) and tool_call_rounds < 3:
        tool_call_rounds += 1
        messages.append(ai_response)

        for tc in ai_response.tool_calls:
            fn_name   = tc["name"]
            fn_args   = tc["args"]
            tool_call_id = tc["id"]

            matched = next((t for t in ALL_TOOLS if t.name == fn_name), None)
            if matched:
                try:
                    tool_result = matched.invoke(fn_args)
                except Exception as exc:
                    tool_result = {"status": "error", "message": str(exc)}
            else:
                tool_result = {"status": "error", "message": f"Unknown tool: {fn_name}"}

            messages.append(ToolMessage(
                content=json.dumps(tool_result, default=str),
                tool_call_id=tool_call_id,
            ))

        ai_response = _invoke_with_retry(_llm_with_tools, messages)

    summary: str = (
        ai_response.content
        if isinstance(ai_response.content, str)
        else json.dumps(ai_response.content, default=str)
    )

    return {
        **state,
        "execution_results":  raw_results,
        "execution_summary":  summary,
        "messages":           state.get("messages", []) + [ai_response],
    }