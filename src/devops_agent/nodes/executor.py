from __future__ import annotations

from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.state import AgentState


def executor_node(state: AgentState) -> AgentState:
    adapter = MCPAdapter()
    plan = state.get("plan", [])
    dry_run = state.get("dry_run", True)

    results = []
    for step in plan:
        tool = step.get("tool", "unknown")
        action = step.get("action", "unknown")
        args = step.get("args", {})
        results.append(adapter.run(tool=tool, action=action, args=args, dry_run=dry_run))

    state["execution_results"] = results
    return state
