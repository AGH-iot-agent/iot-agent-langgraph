from __future__ import annotations

from devops_agent.state import AgentState


def finalizer_node(state: AgentState) -> AgentState:
    results = state.get("execution_results", [])
    if results:
        gh_summary = next(
            (
                item
                for item in results
                if item.get("tool") == "github" and item.get("action") == "get_run_summary"
            ),
            None,
        )
        gh_comment = next(
            (
                item
                for item in results
                if item.get("tool") == "github" and item.get("action") == "comment_pr_or_issue"
            ),
            None,
        )

        if gh_summary and gh_summary.get("status") == "ok":
            summary = gh_summary.get("summary_markdown", "")
            if gh_comment:
                comment_status = gh_comment.get("status")
                comment_url = gh_comment.get("comment_url")
                summary = (
                    f"{summary}\n\n"
                    f"### PR/Issue comment\n"
                    f"- status: {comment_status}\n"
                    f"- url: {comment_url or 'n/a'}"
                )
            state["final_summary"] = summary
            return state

        state["final_summary"] = "Plan executed (or simulated) successfully."
        return state

    if state.get("critique_passed", False):
        state["final_summary"] = "Plan approved by critic but no execution results found."
        return state

    notes = state.get("critique_notes", [])
    suffix = "; ".join(notes) if notes else "No details available."
    state["final_summary"] = f"Plan rejected by critic after max revisions. {suffix}"
    return state
