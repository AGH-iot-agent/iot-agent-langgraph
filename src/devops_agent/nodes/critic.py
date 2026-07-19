from __future__ import annotations

import os

from devops_agent.state import AgentState


FORBIDDEN_ACTIONS = {
    "delete_namespace",
    "force_push_main",
    "cluster_admin_apply",
    "delete_persistentvolumeclaim",
    "delete_pvc",
    "patch_node",
    "taint_node",
}

WRITE_ACTIONS = {
    "restart_deployment",
    "scale_deployment",
    "patch_hpa_min_replicas",
    "apply_manifest",
    "helm_upgrade",
}


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _is_forbidden_action(action: str) -> bool:
    normalized = action.strip().lower()
    if normalized in FORBIDDEN_ACTIONS:
        return True

    blocked_substrings = (
        "delete_namespace",
        "delete_pvc",
        "persistentvolumeclaim",
        "patch_node",
        "taint_node",
        "cluster_admin",
    )
    return any(chunk in normalized for chunk in blocked_substrings)

def critic_node(state: AgentState) -> AgentState:
    notes: list[str] = []
    plan = state.get("plan", [])
    state_dry_run = state.get("dry_run", True)
    allowed_namespace = os.environ.get("KUBERNETES_NAMESPACE", "iotag-dev")
    autoapply_enabled = _is_truthy(os.environ.get("AGENT_AUTOAPPLY"))

    for step in plan:
        action = step.get("action", "")
        args = step.get("args", {})
        if _is_forbidden_action(action):
            notes.append(f"Forbidden action detected: {action}")

        if state_dry_run and action in WRITE_ACTIONS and args.get("dry_run") is False:
            notes.append(f"Dry-run policy violation: {action} requested with dry_run=False")

        if action in WRITE_ACTIONS:
            step_namespace = args.get("namespace")
            if step_namespace and step_namespace != allowed_namespace:
                notes.append(
                    f"Namespace policy violation: {action} targets '{step_namespace}', allowed='{allowed_namespace}'"
                )

        if not state_dry_run and action in WRITE_ACTIONS:
            if not autoapply_enabled:
                notes.append(
                    f"Auto-apply policy violation: {action} requires AGENT_AUTOAPPLY=true for dry_run=False"
                )
            if allowed_namespace != "iotag-dev":
                notes.append(
                    "Auto-apply policy violation: live writes are only allowed when KUBERNETES_NAMESPACE=iotag-dev"
                )


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
