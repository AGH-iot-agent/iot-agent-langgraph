from __future__ import annotations

import logging
from typing import Any

from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.state import AgentState
from devops_agent.validators import validate_fix_proposal

logger = logging.getLogger(__name__)

_adapter = MCPAdapter()


def sandbox_validator_node(state: AgentState) -> AgentState:
    """Validate whether proposed YAML changes are deployable in the target sbx namespace."""
    proposal: dict[str, Any] = state.get("ci_fix_proposal") or {}
    context: dict[str, Any] = state.get("context", {})

    logger.info(
        "[SANDBOX] start proposal_files=%d paths=%s root_cause=%s chart_source=%s",
        len(proposal.get("files", [])),
        [str(entry.get("path", "")) for entry in proposal.get("files", [])],
        proposal.get("root_cause", ""),
        proposal.get("_helm_chart_source") or context.get("helm_chart_source") or "",
    )

    validation_result = validate_fix_proposal(
        _adapter,
        proposal=proposal,
        context=context,
    )

    logger.info(
        "[SANDBOX] validation passed=%s errors=%d",
        validation_result.get("passed", False),
        len(validation_result.get("errors", [])),
    )
    if not validation_result.get("passed", False):
        logger.warning("[SANDBOX] validation errors: %s", validation_result.get("errors", []))
        logger.warning("[SANDBOX] validation result keys: %s", sorted(validation_result.keys()))
        output = str(validation_result.get("output", "") or "")
        if output:
            logger.warning("[SANDBOX] validation output (first 1200 chars): %s", output[:1200])

    validation_history = list(state.get("validation_history") or [])
    validation_history.append(validation_result)

    return {
        **state,
        "validation_result": validation_result,
        "validation_history": validation_history,
    }


def route_after_validation(state: AgentState) -> str:
    """Route based on validation result and event kind.

    - Validation failed + attempts remain -> ci_fixer (retry with error feedback)
    - PR build failure event -> pr_fix_commenter (comment on existing PR, always)
    - All other events: validation MUST pass before creating a PR.
      If validation failed after all retries, route to finalizer.
    """
    validation = state.get("validation_result") or {}
    fix_attempt: int = state.get("fix_attempt", 0)
    max_fix_attempts: int = state.get("max_fix_attempts", 5)

    if not validation.get("passed") and fix_attempt < max_fix_attempts:
        logger.info(
            "[SANDBOX] Validation failed (attempt %d/%d) - retrying ci_fixer with error feedback",
            fix_attempt, max_fix_attempts,
        )
        return "ci_fixer"

    event_kind = state.get("event_kind", "")
    if event_kind == "github_pr_build_failure":
        return "pr_fix_commenter"

    proposal = state.get("ci_fix_proposal") or {}
    if not proposal.get("files"):
        return "finalizer"

    if not validation.get("passed"):
        logger.warning(
            "[SANDBOX] Validation failed after %d attempts - blocking PR creation. Errors: %s",
            fix_attempt, validation.get("errors", []),
        )
        return "finalizer"

    return "pr_creator"
