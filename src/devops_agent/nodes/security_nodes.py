"""
Security nodes for LangGraph
=============================

security_input_node
    Entry-point guard: scans the incoming ``request`` field for prompt
    injection and jailbreak attempts before the planner ever sees it.
    Sets ``security_blocked = True`` and skips straight to the finalizer
    when a high/critical threat is found.

security_output_node
    Exit-point guard: sanitises ``final_summary`` / ``execution_summary``
    for secret-leakage and PII before the result is returned to the caller.

route_after_security_input
    Router used in the graph: either "planner" (safe) or "finalizer" (blocked).
"""
from __future__ import annotations

import logging
from typing import Literal

from devops_agent.guardrails.guardrails import run_output_guard
from devops_agent.security import SecurityLayer, security_layer
from devops_agent.state import AgentState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input security node (entry gate)
# ---------------------------------------------------------------------------

def security_input_node(state: AgentState) -> AgentState:
    """
    Scan the incoming request for prompt injection and jailbreak attempts.
    Also scans ``issue_body`` when the event carries GitHub issue content.

    If a high/critical violation is detected the state is marked as blocked
    and the run is short-circuited to the finalizer via ``route_after_security_input``.
    """
    existing_violations: list[dict] = list(state.get("security_violations") or [])

    texts_to_scan: list[tuple[str, str]] = [
        (state.get("request", ""), "user_request"),
        (state.get("issue_body", ""), "issue_body"),
        (state.get("issue_title", ""), "issue_title"),
    ]

    new_violations: list[dict] = []
    blocked = False

    for text, source in texts_to_scan:
        if not text:
            continue
        result = security_layer.scan_input(text, source=source)
        if result.violations:
            new_violations.extend(SecurityLayer.violations_to_dicts(result.violations))
        if not result.is_safe:
            blocked = True
            logger.error(
                "[SECURITY] Input blocked at entry. source=%s violations=%s",
                source, [v["threat_type"] for v in SecurityLayer.violations_to_dicts(result.violations)],
            )
            # Replace the offending field with the sanitised version to avoid
            # forwarding injection payloads even when the run is not halted
            # for lower-severity matches.
            if source == "user_request":
                state = {**state, "request": result.sanitized_text}  # type: ignore[assignment]
            elif source == "issue_body":
                state = {**state, "issue_body": result.sanitized_text}  # type: ignore[assignment]
            elif source == "issue_title":
                state = {**state, "issue_title": result.sanitized_text}  # type: ignore[assignment]

    return {
        **state,  # type: ignore[misc]
        "security_violations": existing_violations + new_violations,
        "security_blocked": blocked,
    }


def route_after_security_input(state: AgentState) -> Literal["planner", "finalizer"]:
    """Route to 'finalizer' when the request was flagged as unsafe."""
    if state.get("security_blocked"):
        return "finalizer"
    return "planner"


# ---------------------------------------------------------------------------
# Output security node (exit sanitizer)
# ---------------------------------------------------------------------------

def security_output_node(state: AgentState) -> AgentState:
    """
    Sanitise ``final_summary`` and ``execution_summary`` before they leave
    the graph.  Replaces secrets and PII with redaction tokens and appends
    any new violations to ``security_violations``.
    """
    existing_violations: list[dict] = list(state.get("security_violations") or [])
    new_violations: list[dict] = []

    fields_to_sanitize = ["final_summary", "execution_summary"]
    updates: dict = {}

    for field_name in fields_to_sanitize:
        text = state.get(field_name, "") or ""
        if not text:
            continue

        # Pass 1 – fast regex scan (always available)
        regex_result = security_layer.scan_output(text)
        text_after_regex = regex_result.sanitized_text
        field_violations = list(regex_result.violations)

        # Pass 2 – deep Guardrails AI scan (Presidio PII + detect-secrets)
        # Operates on the already regex-sanitised text to avoid double reporting.
        guard_result = run_output_guard(text_after_regex)
        if not guard_result.is_safe:
            field_violations.extend(guard_result.violations)
        # Use Guard-sanitised text if it performed additional redactions
        final_text = guard_result.sanitized_text if guard_result.sanitized_text else text_after_regex

        if field_violations:
            new_violations.extend(SecurityLayer.violations_to_dicts(field_violations))
            logger.warning(
                "[SECURITY] Output sanitised field='%s' violations=%s",
                field_name,
                [v["threat_type"] for v in SecurityLayer.violations_to_dicts(field_violations)],
            )
        # Always store the (possibly sanitised) version
        updates[field_name] = final_text

    return {
        **state,  # type: ignore[misc]
        **updates,
        "security_violations": existing_violations + new_violations,
    }
