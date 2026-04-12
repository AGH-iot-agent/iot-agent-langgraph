from __future__ import annotations

from langgraph.graph import END, StateGraph

from devops_agent.nodes.critic import critic_node, route_after_critic
from devops_agent.nodes.executor import executor_node
from devops_agent.nodes.finalizer import finalizer_node
from devops_agent.nodes.planner import planner_node
from devops_agent.state import AgentState


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("planner", planner_node)
    graph.add_node("critic", critic_node)
    graph.add_node("executor", executor_node)
    graph.add_node("finalizer", finalizer_node)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "critic")
    graph.add_conditional_edges(
        "critic",
        route_after_critic,
        {
            "planner": "planner",
            "executor": "executor",
            "finalizer": "finalizer",
        },
    )
    graph.add_edge("executor", "finalizer")
    graph.add_edge("finalizer", END)

    return graph.compile()
