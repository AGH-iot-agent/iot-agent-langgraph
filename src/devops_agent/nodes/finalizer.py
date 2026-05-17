from __future__ import annotations

from devops_agent.state import AgentState


def finalizer_node(state: AgentState) -> AgentState:
    # Security block takes priority — never leak execution details in this case
    if state.get("security_blocked"):
        state["final_summary"] = (
            "Request blocked by the AI security layer. "
            "Detected a high-severity threat (prompt injection, jailbreak, or similar). "
            "No actions were taken."
        )
        return state

    execution_summary = state.get("execution_summary", "")
    if execution_summary:
        state["final_summary"] = execution_summary
        return state

    if not state.get("critique_passed", False):
        notes = state.get("critique_notes", [])
        suffix = "; ".join(notes) if notes else "No details available."
        state["final_summary"] = f"Plan rejected by critic after max revisions. {suffix}"
        return state

    state["final_summary"] = "Plan approved but no execution results found."
    return state
