from __future__ import annotations

import os
import re
from typing import Any

from devops_agent.env_flags import guardrails_enabled
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

# Text-publishing tools: their arguments are the agent's own prose (refusal notices,
# diagnoses) rather than an infrastructure command, so the payload must not be treated
# as a request to execute whatever action name it happens to mention.
NARRATIVE_TOOLS = frozenset({
    "create_issue_comment",
    "create_issue",
    "update_issue",
})

WRITE_ACTIONS = {
    "restart_deployment",
    "scale_deployment",
    "patch_hpa_min_replicas",
    "apply_manifest",
    "helm_upgrade",
}


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


_KUBECTL_DELETE_NS_RE = re.compile(
    r"kubectl\s+delete\s+n(?:s|amespace)\b",
    re.IGNORECASE,
)


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
        "force_push_main",
    )
    return any(chunk in normalized for chunk in blocked_substrings)


def forbidden_execute_name(action: str, args: Any = None, *, scan_args: bool | None = None) -> str | None:
    """Canonical name if this tool must never be executed (even with guardrails off).

    Narrative tools carry prose the agent writes to *report* a refusal, so their payload is
    not scanned: doing so intercepts the very comment that says the action was blocked.
    The tool name itself is still checked.
    """
    normalized = str(action or "").strip().lower()
    if scan_args is None:
        scan_args = normalized not in NARRATIVE_TOOLS
    blob = f"{normalized} {args!s}".lower() if scan_args else normalized
    if "delete_namespace" in blob or _KUBECTL_DELETE_NS_RE.search(blob):
        return "delete_namespace"
    if "force_push_main" in blob:
        return "force_push_main"
    if not _is_forbidden_action(normalized) and not _is_forbidden_action(blob):
        return None
    for name in FORBIDDEN_ACTIONS:
        if name in blob:
            return name
    return normalized or None


def intercepted_forbidden_result(name: str) -> dict[str, Any]:
    return {
        "status": "intercepted",
        "executed": False,
        "unsafe_action": name,
        "message": (
            f"Forbidden tool {name} was requested but not executed "
            "(executor safety intercept)."
        ),
    }

def critic_node(state: AgentState) -> AgentState:
    notes: list[str] = []
    plan = state.get("plan", [])
    state_dry_run = state.get("dry_run", True)
    allowed_namespace = os.environ.get("KUBERNETES_NAMESPACE", "iotag-dev")
    autoapply_enabled = _is_truthy(os.environ.get("AGENT_AUTOAPPLY"))

    for step in plan:
        action = step.get("action", "")
        args = step.get("args", {})
        if guardrails_enabled() and _is_forbidden_action(action):
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
