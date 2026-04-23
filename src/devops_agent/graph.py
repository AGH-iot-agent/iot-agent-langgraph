# devops_agent/graph.py
from __future__ import annotations

from langchain_core.tools import tool
from langchain_anthropic import ChatAnthropic
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.nodes.critic import critic_node, route_after_critic
from devops_agent.nodes.executor import executor_node
from devops_agent.nodes.finalizer import finalizer_node
from devops_agent.nodes.planner import planner_node
from devops_agent.state import AgentState

from devops_agent.graph_tools import ALL_TOOLS


import logging
logger = logging.getLogger("devops_agent.graph")

def build_graph():
    logger.info("Creating LangGraph model and custom nodes...")
    graph = StateGraph(AgentState)

    def log_node(name, fn):
        def wrapper(state):
            logger.info(f"[LangGraph] Node: {name}, state keys: {list(state.keys())}")
            return fn(state)
        return wrapper

    graph.add_node("planner",   log_node("planner", planner_node))
    graph.add_node("critic",    log_node("critic", critic_node))
    graph.add_node("executor",  log_node("executor", executor_node))
    graph.add_node("finalizer", log_node("finalizer", finalizer_node))
    graph.add_node("tools",     ToolNode(ALL_TOOLS))

    graph.set_entry_point("planner")

    graph.add_edge("planner", "critic")

    def log_route_after_critic(state):
        result = route_after_critic(state)
        logger.info(f"[LangGraph] Routing after critic: {result}, revision_count={state.get('revision_count')}")
        return result

    def log_route_executor(state):
        result = _route_executor(state)
        logger.info(f"[LangGraph] Routing after executor: {result}, messages={len(state.get('messages', []))}")
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
            "finalizer": "finalizer",
        },
    )
    graph.add_edge("tools",     "executor")
    graph.add_edge("finalizer", END)

    logger.info("LangGraph compilation completed.")
    return graph.compile()


def _route_executor(state: AgentState) -> str:
    """If the LLM returned tool_calls, execute them; otherwise finalize."""
    messages = state.get("messages", [])
    if messages and getattr(messages[-1], "tool_calls", None):
        return "tools"
    return "finalizer"