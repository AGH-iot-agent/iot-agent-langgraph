from __future__ import annotations

from devops_agent.state import AgentState


FORBIDDEN_ACTIONS = {
    "delete_namespace",
    "force_push_main",
    "cluster_admin_apply",
}


def critic_node(state: AgentState) -> AgentState:
    notes: list[str] = []
    plan = state.get("plan", [])

    for step in plan:
        action = step.get("action", "")
        if action in FORBIDDEN_ACTIONS:
            notes.append(f"Forbidden action detected: {action}")

    if not state.get("dry_run", True):
        notes.append("Write mode enabled: approval gate required.")

    passed = len(notes) == 0

    state["critique_notes"] = notes
    state["critique_passed"] = passed
    return state


def route_after_critic(state: AgentState) -> str:
    if state.get("critique_passed", False):
        return "executor"

    current = state.get("revision_count", 0)
    max_revisions = state.get("max_revisions", 2)

    if current < max_revisions:
        state["revision_count"] = current + 1
        return "planner"

    return "finalizer"
