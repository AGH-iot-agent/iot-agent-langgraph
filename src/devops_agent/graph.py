# devops_agent/graph.py
from __future__ import annotations

import os
import time
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from devops_agent.metrics import agent_metrics
from devops_agent.nodes.critic import critic_node, route_after_critic
from devops_agent.nodes.executor import executor_node
from devops_agent.nodes.finalizer import finalizer_node
from devops_agent.nodes.planner import planner_node
from devops_agent.nodes.ci_fixer import ci_fixer_node
from devops_agent.nodes.sandbox_validator import sandbox_validator_node, route_after_validation
from devops_agent.nodes.pr_creator import pr_creator_node
from devops_agent.nodes.pr_fix_commenter import pr_fix_commenter_node
from devops_agent.nodes.security_nodes import (
    security_input_node,
    security_output_node,
    route_after_security_input,
)
from devops_agent.state import AgentState
from devops_agent.graph_tools import ALL_TOOLS

import logging
logger = logging.getLogger("devops_agent.graph")


def get_agent_mode() -> str:
    """single_agent | multi_agent -- eksperymentalny prze\u0142\u0105cznik topologii grafu (RQ1-3)."""
    mode = os.environ.get("AGENT_MODE", "multi_agent").strip().lower()
    return mode if mode in ("single_agent", "multi_agent") else "multi_agent"


def record_graph_run_metrics(state: dict[str, Any], *, started_at: float | None = None, trace_id: str | None = None, event_kind: str | None = None, repo: str | None = None, title: str | None = None) -> None:
    """Record one telemetry event for a completed LangGraph execution."""
    if not isinstance(state, dict):
        return

    started = state.get("started_at") if started_at is None else started_at
    if started is None:
        started = time.time()

    trace = str(trace_id or state.get("trace_id") or "no-trace")
    kind = str(event_kind or state.get("event_kind") or "manual_run")
    repo_name = str(repo or ((state.get("context") or {}).get("repo_full_name") or ""))
    title_text = str(title or state.get("issue_title") or state.get("request") or "unknown")

    token_usage = state.get("token_usage") or {}
    input_tokens = int(token_usage.get("input_tokens", 0) or 0)
    output_tokens = int(token_usage.get("output_tokens", 0) or 0)
    plan = state.get("plan") or []
    validation = state.get("validation_result") or {}

    agent_metrics.record({
        "trace_id": trace,
        "event_kind": kind,
        "repo": repo_name,
        "title": title_text,
        "mttr_s": max(0.0, time.time() - float(started)),
        "plan_step_count": int(token_usage.get("plan_step_count", len(plan))),
        "tool_call_rounds": int(token_usage.get("tool_call_rounds", 0) or 0),
        "revision_count": int(state.get("revision_count", 0) or 0),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "validation_passed": bool(validation.get("passed", False)),
        "pr_created": bool(state.get("pr_url")),
        "agent_mode": str(state.get("agent_mode") or get_agent_mode()),
    })


def build_graph(agent_mode: str | None = None):
    mode = (agent_mode or get_agent_mode())
    logger.info("Creating LangGraph model and custom nodes (agent_mode=%s)", mode)
    graph = StateGraph(AgentState)

    def log_node(name, fn):
        def wrapper(state):
            trace_id = state.get("trace_id", "no-trace") if isinstance(state, dict) else "no-trace"
            logger.debug("[LangGraph] enter node=%s trace_id=%s", name, trace_id)
            try:
                result = fn(state)
                logger.debug("[LangGraph] exit node=%s trace_id=%s", name, trace_id)
                return result
            except Exception:
                logger.exception("[LangGraph] node failed node=%s trace_id=%s", name, trace_id)
                raise
        return wrapper

    def _finalizer_with_metrics(state):
        updated = finalizer_node(state)
        if isinstance(updated, dict):
            updated.setdefault("started_at", time.time())
            updated.setdefault("agent_mode", mode)
            record_graph_run_metrics(updated)
        return updated

    def _security_input_with_mode(state):
        tagged = {**state, "agent_mode": mode} if isinstance(state, dict) else state
        result = security_input_node(tagged)
        if isinstance(result, dict):
            result.setdefault("agent_mode", mode)
        return result

    # Security gates (entry + exit)
    graph.add_node("security_input",    log_node("security_input",    _security_input_with_mode))
    graph.add_node("security_output",   log_node("security_output",   security_output_node))
    graph.add_node("planner",           log_node("planner", planner_node))
    graph.add_node("executor",          log_node("executor", executor_node))
    graph.add_node("finalizer",         log_node("finalizer", _finalizer_with_metrics))
    graph.add_node("ci_fixer",          log_node("ci_fixer", ci_fixer_node))
    graph.add_node("pr_creator",        log_node("pr_creator", pr_creator_node))
    graph.add_node("pr_fix_commenter",  log_node("pr_fix_commenter", pr_fix_commenter_node))
    graph.add_node("tools",             ToolNode(ALL_TOOLS))
    if mode == "multi_agent":
        graph.add_node("critic",            log_node("critic", critic_node))
        graph.add_node("sandbox_validator", log_node("sandbox_validator", sandbox_validator_node))

    graph.set_entry_point("security_input")

    graph.add_conditional_edges(
        "security_input",
        route_after_security_input,
        {
            "planner":   "planner",
            "finalizer": "finalizer",
        },
    )

    def log_route_after_critic(state):
        result = route_after_critic(state)
        logger.debug(
            "[LangGraph] route after critic=%s trace_id=%s revision_count=%s",
            result,
            state.get("trace_id", "no-trace"),
            state.get("revision_count"),
        )
        return result

    def log_route_executor(state):
        result = _route_executor(state)
        logger.debug(
            "[LangGraph] route after executor=%s trace_id=%s messages=%s",
            result,
            state.get("trace_id", "no-trace"),
            len(state.get("messages", [])),
        )
        return result

    def log_route_after_validation(state):
        result = route_after_validation(state)
        logger.debug(
            "[LangGraph] route after validation=%s trace_id=%s",
            result,
            state.get("trace_id", "no-trace"),
        )
        return result

    def log_route_after_ci_fixer_single_agent(state):
        result = _route_after_ci_fixer_single_agent(state)
        logger.debug(
            "[LangGraph] route after ci_fixer (single_agent)=%s trace_id=%s",
            result,
            state.get("trace_id", "no-trace"),
        )
        return result

    if mode == "single_agent":
        # Uproszczony graf: bez critic (polityka ADR-003) i bez sandbox_validator
        # (brak dry-run gate i p\u0119tli samonaprawy) - eksperymentalny baseline (RQ1-3).
        graph.add_edge("planner", "executor")
    else:
        graph.add_edge("planner", "critic")
        graph.add_conditional_edges(
            "critic",
            log_route_after_critic,
            {
                "planner":   "planner",
                "executor":  "executor",
                "finalizer": "finalizer",
            },
        )

    graph.add_conditional_edges(
        "executor",
        log_route_executor,
        {
            "tools":     "tools",
            "ci_fixer":  "ci_fixer",
            "finalizer": "finalizer",
        },
    )

    graph.add_edge("tools",     "executor")

    if mode == "single_agent":
        graph.add_conditional_edges(
            "ci_fixer",
            log_route_after_ci_fixer_single_agent,
            {
                "pr_creator":       "pr_creator",
                "pr_fix_commenter": "pr_fix_commenter",
                "finalizer":        "finalizer",
            },
        )
    else:
        graph.add_edge("ci_fixer", "sandbox_validator")
        graph.add_conditional_edges(
            "sandbox_validator",
            log_route_after_validation,
            {
                "ci_fixer":         "ci_fixer",
                "pr_creator":       "pr_creator",
                "pr_fix_commenter": "pr_fix_commenter",
                "finalizer":        "finalizer",
            },
        )
    graph.add_edge("pr_creator",       "finalizer")
    graph.add_edge("pr_fix_commenter", "finalizer")
    graph.add_edge("finalizer",        "security_output")
    graph.add_edge("security_output",  END)

    logger.info("LangGraph compilation completed")
    return graph.compile()


def _route_executor(state: AgentState) -> str:
    """Route after executor: tool calls → tools; CI/PR build failure events → ci_fixer; else finalizer.

    github_issue events are fully handled by executor (produces a comment via execution_summary)
    and MUST NOT reach ci_fixer or pr_creator — those nodes only know how to fix CI failures.
    """
    messages = state.get("messages", [])
    if messages and getattr(messages[-1], "tool_calls", None):
        return "tools"
    if state.get("event_kind") in (
        "github_ci_failure", "github_pr_build_failure"
    ):
        return "ci_fixer"
    return "finalizer"


def _route_after_ci_fixer_single_agent(state: AgentState) -> str:
    """Route after ci_fixer when running without sandbox_validator (single_agent mode).

    Mirrors route_after_validation's non-validation branches: no dry-run gate, no
    self-heal retry loop - the fix is used as-is, exactly like a lone agent would.
    """
    event_kind = state.get("event_kind", "")
    if event_kind == "github_pr_build_failure":
        return "pr_fix_commenter"

    proposal = state.get("ci_fix_proposal") or {}
    if not proposal.get("files"):
        return "finalizer"

    return "pr_creator"
