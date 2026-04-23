from __future__ import annotations

import json
import logging
from typing import Any
from pathlib import Path

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.state import AgentState
from devops_agent.graph_tools import ALL_TOOLS
from devops_agent.prompts import _build_system_prompt

logger = logging.getLogger(__name__)

_llm = ChatOpenAI(model="gpt-3.5-turbo-0125", temperature=0, max_tokens=512)
_llm_with_tools = _llm.bind_tools(ALL_TOOLS)
_adapter = MCPAdapter()

def load_prompt(name: str) -> str:
    return Path(f"../prompts/{name}.md").read_text(encoding="utf-8").strip()

def _build_system_prompt(event_kind: str) -> str:
    base_rules = load_prompt("base_rules")

    specific_path = f"../prompts/{event_kind}.md"
    specific = Path(specific_path).read_text(encoding="utf-8").strip() \
        if Path(specific_path).exists() else ""

    return "\n\n".join([
        base_rules,
        specific
    ])

def executor_node(state: AgentState) -> AgentState:
    plan: list[dict[str, Any]] = state.get("plan", [])
    dry_run: bool = state.get("dry_run", True)
    original_request: str = state.get("request", "")
    event_kind = state.get("event_kind", "default") 

    if not plan:
        logger.warning("[EXECUTOR] No plan to execute")
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

        logger.info("[EXECUTOR] Step %d/%d — %s/%s args=%s dry_run=%s",
                    i + 1, len(plan), tool, action, args, dry_run)

        result = _adapter.run(service=tool, tool=action, params=args, dry_run=dry_run)

        if result.get("status") == "error" and "Unknown action" in result.get("message", ""):
            continue

        logger.info("[EXECUTOR] Step %d result status=%s", i + 1, result.get("status"))
        raw_results.append({
            "step":   i + 1,
            "tool":   tool,
            "action": action,
            "args":   args,
            "result": result,
        })

    # Usuń kroki z błędem Unknown tool
    meaningful_results = [
        r for r in raw_results
        if not (
            r.get("result", {}).get("status") == "error"
            and "Unknown tool" in r.get("result", {}).get("message", "")
        )
    ]

    # Dla github_issue odfiltruj kubernetes
    if event_kind == "github_issue":
        meaningful_results = [
            r for r in meaningful_results
            if r.get("tool") != "kubernetes"
        ]

    results_block = json.dumps(meaningful_results, indent=2, default=str)
    plan_block    = json.dumps(plan,        indent=2, default=str)

    messages = [
        SystemMessage(content=_build_system_prompt(event_kind)),
        HumanMessage(content=(
            f"## Issue\n"
            f"**Tytuł:** {state.get('issue_title', '')}\n\n"
            f"**Treść:**\n{state.get('issue_body', '')}\n\n"
            f"---\n"
            f"## Wyniki narzędzi\n"
            f"{results_block}\n\n"
            f"Napisz komentarz GitHub odnoszący się do tego konkretnego problemu."
        ))
    ]

    logger.info("[EXECUTOR] Sending results to LLM for interpretation")
    ai_response: AIMessage = _llm_with_tools.invoke(messages)

    tool_call_rounds = 0
    while getattr(ai_response, "tool_calls", None) and tool_call_rounds < 3:
        tool_call_rounds += 1
        messages.append(ai_response)

        for tc in ai_response.tool_calls:
            fn_name   = tc["name"]
            fn_args   = tc["args"]
            tool_call_id = tc["id"]

            logger.info("[EXECUTOR] LLM tool call: %s(%s)", fn_name, fn_args)

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

        ai_response = _llm_with_tools.invoke(messages)

    summary: str = (
        ai_response.content
        if isinstance(ai_response.content, str)
        else json.dumps(ai_response.content, default=str)
    )

    logger.info("[EXECUTOR] Summary: %s", summary[:200])

    return {
        **state,
        "execution_results":  raw_results,
        "execution_summary":  summary,
        "messages":           state.get("messages", []) + [ai_response],
    }