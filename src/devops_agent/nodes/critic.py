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


    passed = len(notes) == 0

    new_revision_count = state.get("revision_count", 0)
    if not passed:
        new_revision_count += 1

    return {
        **state,
        "critique_notes": notes,
        "critique_passed": passed,
        "revision_count": new_revision_count,
    }


def route_after_critic(state: AgentState) -> str:
    if state.get("critique_passed", False):
        return "executor"

    current = state.get("revision_count", 0)
    max_revisions = state.get("max_revisions", 2)

    if current < max_revisions:
        return "planner"
    return "finalizer"
