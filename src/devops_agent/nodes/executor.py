from __future__ import annotations

import json
import logging
import os
from typing import Any
from pathlib import Path

from devops_agent.graph_tools import ALL_TOOLS
from devops_agent.llm_utils import invoke_with_retry as _invoke_with_retry
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.nodes.critic import forbidden_execute_name, intercepted_forbidden_result
from devops_agent.state import AgentState
from devops_agent.prompts import _build_system_prompt


logger = logging.getLogger(__name__)

_llm: ChatOpenAI | None = None
_llm_with_tools = None
_adapter = MCPAdapter()
_DEFAULT_LLM_MODEL = os.getenv("DEVOPS_AGENT_MODEL", "gpt-4o-mini")


def _get_llm_with_tools():
    """Lazily create the LLM so OPENAI_API_KEY is read at call time, not at import time."""
    global _llm, _llm_with_tools
    if _llm_with_tools is None:
        _llm = ChatOpenAI(model=_DEFAULT_LLM_MODEL, temperature=0, max_tokens=2000)
        _llm_with_tools = _llm.bind_tools(ALL_TOOLS)
    return _llm_with_tools


def load_prompt(name: str) -> str:
    return Path(f"../prompts/{name}.md").read_text(encoding="utf-8").strip()


def _run_plan_steps(
    plan: list[dict[str, Any]],
    dry_run: bool,
    intercepted: list[str] | None = None,
) -> list[dict[str, Any]]:
    raw_results: list[dict[str, Any]] = []
    intercepted = intercepted if intercepted is not None else []
    for index, step in enumerate(plan):
        tool = step.get("tool", "unknown")
        action = step.get("action", "unknown")
        args = step.get("args", {})

        lethal = forbidden_execute_name(str(action), args)
        if lethal:
            if lethal not in intercepted:
                intercepted.append(lethal)
            logger.error(
                "[EXECUTOR] Intercepted forbidden plan step action=%s tool=%s (not executed)",
                action,
                tool,
            )
            raw_results.append({
                "step": index + 1,
                "tool": tool,
                "action": action,
                "args": args,
                "result": intercepted_forbidden_result(lethal),
            })
            continue

        result = _adapter.run(service=tool, tool=action, params=args, dry_run=dry_run)
        if result.get("status") == "error" and "Unknown action" in result.get("message", ""):
            continue

        raw_results.append({
            "step": index + 1,
            "tool": tool,
            "action": action,
            "args": args,
            "result": result,
        })

    return raw_results


def _is_meaningful_result(result_entry: dict[str, Any]) -> bool:
    result = result_entry.get("result", {})
    if result.get("status") == "error" and "Unknown tool" in result.get("message", ""):
        return False
    if result.get("status") == "error" and result_entry.get("action") == "get_file_content":
        message = result.get("message", "").lower()
        if "not found" in message or "404" in message:
            return False
    return True


_NOISY_KUBERNETES_ACTIONS_FOR_ISSUES = {"get_pods", "get_events"}

def _filter_meaningful_results(raw_results: list[dict[str, Any]], event_kind: str) -> list[dict[str, Any]]:
    meaningful_results = [result for result in raw_results if _is_meaningful_result(result)]
    if event_kind == "github_issue":
        meaningful_results = [
            result for result in meaningful_results
            if result.get("tool") != "kubernetes"
            or result.get("action") not in _NOISY_KUBERNETES_ACTIONS_FOR_ISSUES
        ]
    return meaningful_results


def _trim_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n[... truncated ...]"


def _build_messages(state: AgentState, results_block: str) -> list[Any]:
    event_kind = state.get("event_kind", "default")
    issue_body = _trim_text(state.get("issue_body", ""), 2000)
    system_prompt = _build_system_prompt(event_kind)

    return [
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
        )),
    ]


def _run_tool_call_rounds(
    ai_response: AIMessage,
    trace_id: str,
    intercepted: list[str] | None = None,
) -> tuple[AIMessage, int, list[Any]]:
    messages: list[Any] = []
    tool_call_rounds = 0
    intercepted = intercepted if intercepted is not None else []

    while getattr(ai_response, "tool_calls", None) and tool_call_rounds < 3:
        tool_call_rounds += 1
        messages.append(ai_response)

        for tool_call in ai_response.tool_calls:
            fn_name = tool_call["name"]
            fn_args = tool_call["args"]
            tool_call_id = tool_call["id"]

            lethal = forbidden_execute_name(str(fn_name), fn_args)
            if lethal:
                if lethal not in intercepted:
                    intercepted.append(lethal)
                logger.error(
                    "[EXECUTOR] Intercepted forbidden LLM tool call tool=%s (not executed)",
                    fn_name,
                )
                tool_result = intercepted_forbidden_result(lethal)
            else:
                matched = next((tool for tool in ALL_TOOLS if tool.name == fn_name), None)
                if matched:
                    try:
                        tool_result = matched.invoke(fn_args)
                    except Exception as exc:
                        logger.exception(
                            "[EXECUTOR] Tool call failed tool=%s round=%s trace_id=%s",
                            fn_name,
                            tool_call_rounds,
                            trace_id,
                        )
                        tool_result = {"status": "error", "message": str(exc)}
                else:
                    logger.error("[EXECUTOR] Unknown tool requested by model: %s", fn_name)
                    tool_result = {"status": "error", "message": f"Unknown tool: {fn_name}"}

            messages.append(ToolMessage(
                content=json.dumps(tool_result, default=str),
                tool_call_id=tool_call_id,
            ))

        ai_response = _invoke_with_retry(_get_llm_with_tools(), messages)

    return ai_response, tool_call_rounds, messages


def _extract_token_usage(ai_response: AIMessage) -> dict[str, int]:
    input_tokens = 0
    output_tokens = 0

    usage_meta = getattr(ai_response, "usage_metadata", None) or {}
    if isinstance(usage_meta, dict):
        input_tokens = int(usage_meta.get("input_tokens") or usage_meta.get("prompt_tokens") or 0)
        output_tokens = int(usage_meta.get("output_tokens") or usage_meta.get("completion_tokens") or 0)

    if input_tokens == 0 and output_tokens == 0:
        response_meta = getattr(ai_response, "response_metadata", None) or {}
        token_usage = response_meta.get("token_usage") if isinstance(response_meta, dict) else None
        if isinstance(token_usage, dict):
            input_tokens = int(token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0)
            output_tokens = int(token_usage.get("completion_tokens") or token_usage.get("output_tokens") or 0)

    return {"input_tokens": input_tokens, "output_tokens": output_tokens}

def executor_node(state: AgentState) -> AgentState:
    plan: list[dict[str, Any]] = state.get("plan", [])
    dry_run: bool = state.get("dry_run", True)

    if not plan:
        return {
            **state,
            "execution_results": [],
            "execution_summary": "No steps in plan — nothing executed.",
            "forbidden_tools_requested": list(state.get("forbidden_tools_requested") or []),
        }

    intercepted: list[str] = list(state.get("forbidden_tools_requested") or [])
    raw_results = _run_plan_steps(plan, dry_run, intercepted)
    event_kind = state.get("event_kind", "default")
    current_usage = state.get("token_usage") or {}
    token_usage = {
        "input_tokens": int(current_usage.get("input_tokens", 0)),
        "output_tokens": int(current_usage.get("output_tokens", 0)),
        "tool_call_rounds": int(current_usage.get("tool_call_rounds", 0)),
        "plan_step_count": int(current_usage.get("plan_step_count", len(plan))),
    }

    # CI/PR-build diagnosis belongs to ci_fixer (JSON + Helm values). The generic
    # executor comment template guesses Spring Boot OOM from values and is what
    # single-agent currently posts as ## gh_action_bot when finalizer overwrites.
    if event_kind in ("github_ci_failure", "github_pr_build_failure"):
        return {
            **state,
            "execution_results": raw_results,
            "execution_summary": "",
            "token_usage": token_usage,
            "messages": state.get("messages", []),
            "forbidden_tools_requested": intercepted,
        }

    meaningful_results = _filter_meaningful_results(raw_results, event_kind)
    results_block = json.dumps(meaningful_results, indent=2, default=str)
    results_block = _trim_text(results_block, 5000)

    messages = _build_messages(state, results_block)

    ai_response: AIMessage = _invoke_with_retry(_get_llm_with_tools(), messages)

    trace_id = str(state.get("trace_id", "no-trace"))
    ai_response, tool_call_rounds, tool_messages = _run_tool_call_rounds(
        ai_response, trace_id, intercepted
    )
    messages.extend(tool_messages)

    usage = _extract_token_usage(ai_response)
    current_usage = state.get("token_usage") or {}
    token_usage = {
        "input_tokens": int(current_usage.get("input_tokens", 0)) + int(usage.get("input_tokens", 0)),
        "output_tokens": int(current_usage.get("output_tokens", 0)) + int(usage.get("output_tokens", 0)),
        "tool_call_rounds": int(current_usage.get("tool_call_rounds", 0)) + int(tool_call_rounds),
        "plan_step_count": int(current_usage.get("plan_step_count", len(plan))),
    }

    summary: str = (
        ai_response.content
        if isinstance(ai_response.content, str)
        else json.dumps(ai_response.content, default=str)
    )

    return {
        **state,
        "execution_results":  raw_results,
        "execution_summary":  summary,
        "token_usage":        token_usage,
        "messages":           state.get("messages", []) + [ai_response],
        "forbidden_tools_requested": intercepted,
    }