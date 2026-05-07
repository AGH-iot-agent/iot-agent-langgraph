# devops_agent/graph.py
from __future__ import annotations

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from devops_agent.nodes.critic import critic_node, route_after_critic
from devops_agent.nodes.executor import executor_node
from devops_agent.nodes.finalizer import finalizer_node
from devops_agent.nodes.planner import planner_node
from devops_agent.nodes.ci_fixer import ci_fixer_node
from devops_agent.nodes.sandbox_validator import sandbox_validator_node, route_after_validation
from devops_agent.nodes.pr_creator import pr_creator_node
from devops_agent.nodes.pr_fix_commenter import pr_fix_commenter_node
from devops_agent.state import AgentState
from devops_agent.graph_tools import ALL_TOOLS

import logging
logger = logging.getLogger("devops_agent.graph")

def _route_entry(state: AgentState) -> str:
    """Route CI failure events directly to executor (planner already ran), others through standard flow."""
    if state.get("event_kind") == "github_ci_failure":
        return "planner"
    return "planner"


def build_graph():
    # logger.info("Creating LangGraph model and custom nodes...")
    graph = StateGraph(AgentState)

    def log_node(name, fn):
        def wrapper(state):
            # logger.info(f"[LangGraph] Node: {name}, state keys: {list(state.keys())}")
            return fn(state)
        return wrapper

    graph.add_node("planner",           log_node("planner", planner_node))
    graph.add_node("critic",            log_node("critic", critic_node))
    graph.add_node("executor",          log_node("executor", executor_node))
    graph.add_node("finalizer",         log_node("finalizer", finalizer_node))
    graph.add_node("tools",             ToolNode(ALL_TOOLS))
    graph.add_node("ci_fixer",          log_node("ci_fixer", ci_fixer_node))
    graph.add_node("sandbox_validator", log_node("sandbox_validator", sandbox_validator_node))
    graph.add_node("pr_creator",        log_node("pr_creator", pr_creator_node))
    graph.add_node("pr_fix_commenter",  log_node("pr_fix_commenter", pr_fix_commenter_node))

    graph.set_entry_point("planner")

    graph.add_edge("planner", "critic")

    def log_route_after_critic(state):
        result = route_after_critic(state)
        # logger.info(f"[LangGraph] Routing after critic: {result}, revision_count={state.get('revision_count')}")
        return result

    def log_route_executor(state):
        result = _route_executor(state)
        # logger.info(f"[LangGraph] Routing after executor: {result}, messages={len(state.get('messages', []))}")
        return result

    def log_route_after_validation(state):
        result = route_after_validation(state)
        # logger.info(f"[LangGraph] Routing after validation: {result}")
        return result

    graph.add_conditional_edges(
        "critic",
        log_route_after_critic,
        {
            "planner":   "planner",
            "executor":  "executor",
            "finalizer": "finalizer",
        },
    )

    graph.add_conditional_edges(
        "executor",
        log_route_executor,
        {
            "tools":     "tools",
            "ci_fixer":  "ci_fixer",
            "finalizer": "finalizer",
        },
    )

    graph.add_edge("tools",     "executor")

    graph.add_edge("ci_fixer", "sandbox_validator")
    graph.add_conditional_edges(
        "sandbox_validator",
        log_route_after_validation,
        {
            "pr_creator":       "pr_creator",
            "pr_fix_commenter": "pr_fix_commenter",
            "finalizer":        "finalizer",
        },
    )
    graph.add_edge("pr_creator",       "finalizer")
    graph.add_edge("pr_fix_commenter", "finalizer")
    graph.add_edge("finalizer",  END)

    # logger.info("LangGraph compilation completed.")
    return graph.compile()


def _route_executor(state: AgentState) -> str:
    """Route after executor: tool calls → tools; CI/PR/issue events → ci_fixer; else finalizer."""
    messages = state.get("messages", [])
    if messages and getattr(messages[-1], "tool_calls", None):
        return "tools"
    if state.get("event_kind") in (
        "github_ci_failure", "github_pr_build_failure", "github_issue", "github_pr"
    ):
        return "ci_fixer"
    return "finalizer"
