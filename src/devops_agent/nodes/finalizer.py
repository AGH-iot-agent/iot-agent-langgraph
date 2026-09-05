from __future__ import annotations

from devops_agent.state import AgentState


def finalizer_node(state: AgentState) -> AgentState:
    # Security block takes priority — never leak execution details in this case
    if state.get("security_blocked"):
        forbidden = []
        for violation in state.get("security_violations") or []:
            if not isinstance(violation, dict):
                continue
            if violation.get("threat_type") != "forbidden_action":
                continue
            action = str(violation.get("matched_pattern") or "").strip()
            if action and action not in forbidden:
                forbidden.append(action)
        if forbidden:
            names = ", ".join(forbidden)
            state["final_summary"] = (
                f"Request blocked: forbidden action {names} is not allowed. "
                "No actions were taken (unsafe_action_blocked)."
            )
            return state
        state["final_summary"] = (
            "Request blocked by the AI security layer. "
            "Detected a high-severity threat (prompt injection, jailbreak, or similar). "
            "No actions were taken."
        )
        return state

    # pr_fix_commenter / pr_creator already wrote the user-facing comment.
    # Do not replace it with the executor's generic Root Cause template.
    if state.get("event_kind") in ("github_ci_failure", "github_pr_build_failure"):
        if state.get("final_summary"):
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
