"""
Security nodes for LangGraph
=============================

security_input_node
    Entry-point guard: scans the incoming ``request`` / ``issue_body`` for
    prompt injection, jailbreak, and forbidden-action requests (e.g.
    ``delete_namespace``) before the planner ever sees it.
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

from devops_agent.env_flags import guardrails_enabled
from devops_agent.guardrails.guardrails import run_output_guard
from devops_agent.security import SecurityLayer, security_layer
from devops_agent.state import AgentState

logger = logging.getLogger(__name__)


def security_input_node(state: AgentState) -> AgentState:
    """
    Scan the incoming request for prompt injection, jailbreak, and
    forbidden-action requests. Also scans ``issue_body`` when the event
    carries GitHub issue content.

    If a high/critical violation is detected the state is marked as blocked
    and the run is short-circuited to the finalizer via ``route_after_security_input``.

    ``GUARDRAILS_ENABLED=0``/``false`` skips blocking (ablation) so the same
    adversarial issue can reach the planner. Detections are still recorded on
    ``requested_forbidden_actions``. Executor intercepts lethal tools separately.
    """
    existing_violations: list[dict] = list(state.get("security_violations") or [])
    enabled = guardrails_enabled()
    requested_forbidden: list[str] = list(state.get("requested_forbidden_actions") or [])

    issue_body = state.get("issue_body", "") or ""
    if not issue_body:
        context = state.get("context") or {}
        issue = context.get("issue") if isinstance(context, dict) else None
        if isinstance(issue, dict):
            issue_body = str(issue.get("body") or "")

    texts_to_scan: list[tuple[str, str]] = [
        (state.get("request", ""), "user_request"),
        (issue_body, "issue_body"),
        (state.get("issue_title", ""), "issue_title"),
    ]

    new_violations: list[dict] = []
    blocked = False

    for text, source in texts_to_scan:
        if not text:
            continue
        result = security_layer.scan_input(text, source=source)
        violation_dicts = SecurityLayer.violations_to_dicts(result.violations) if result.violations else []
        if violation_dicts:
            new_violations.extend(violation_dicts)
            for item in violation_dicts:
                if item.get("threat_type") != "forbidden_action":
                    continue
                name = str(item.get("matched_pattern") or "").strip()
                if name and name not in requested_forbidden:
                    requested_forbidden.append(name)
        if not result.is_safe:
            if not enabled:
                logger.warning(
                    "[SECURITY] Guardrails off — not blocking source=%s violations=%s",
                    source,
                    [v["threat_type"] for v in violation_dicts],
                )
                continue
            blocked = True
            logger.error(
                "[SECURITY] Input blocked at entry. source=%s violations=%s",
                source, [v["threat_type"] for v in violation_dicts],
            )
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
        "requested_forbidden_actions": requested_forbidden,
        "guardrails_enabled": enabled,
    }


def route_after_security_input(state: AgentState) -> Literal["planner", "finalizer"]:
    """Route to 'finalizer' when the request was flagged as unsafe."""
    if state.get("security_blocked"):
        return "finalizer"
    return "planner"


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

        regex_result = security_layer.scan_output(text)
        text_after_regex = regex_result.sanitized_text
        field_violations = list(regex_result.violations)

        guard_result = run_output_guard(text_after_regex)
        if not guard_result.is_safe:
            field_violations.extend(guard_result.violations)

        final_text = guard_result.sanitized_text if guard_result.sanitized_text else text_after_regex

        if field_violations:
            new_violations.extend(SecurityLayer.violations_to_dicts(field_violations))
            logger.warning(
                "[SECURITY] Output sanitised field='%s' violations=%s",
                field_name,
                [v["threat_type"] for v in SecurityLayer.violations_to_dicts(field_violations)],
            )
        updates[field_name] = final_text

    return {
        **state,  # type: ignore[misc]
        **updates,
        "security_violations": existing_violations + new_violations,
    }
