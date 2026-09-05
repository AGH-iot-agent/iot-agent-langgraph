from __future__ import annotations

from devops_agent.graph import (
    _route_after_ci_fixer_single_agent,
    build_graph,
    get_agent_mode,
)


def test_get_agent_mode_defaults_to_multi_agent(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_MODE", raising=False)
    assert get_agent_mode() == "multi_agent"


def test_get_agent_mode_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODE", "single_agent")
    assert get_agent_mode() == "single_agent"


def test_get_agent_mode_rejects_unknown_value(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODE", "not_a_real_mode")
    assert get_agent_mode() == "multi_agent"


def test_single_agent_graph_omits_critic_and_sandbox_validator() -> None:
    graph = build_graph(agent_mode="single_agent")
    nodes = set(graph.get_graph().nodes)
    assert "critic" not in nodes
    assert "sandbox_validator" not in nodes
    assert {"security_input", "planner", "executor", "ci_fixer", "pr_creator", "finalizer"} <= nodes


def test_multi_agent_graph_keeps_critic_and_sandbox_validator() -> None:
    graph = build_graph(agent_mode="multi_agent")
    nodes = set(graph.get_graph().nodes)
    assert {"critic", "sandbox_validator"} <= nodes


def test_route_after_ci_fixer_single_agent_routes_pr_build_failure_to_commenter() -> None:
    state = {"event_kind": "github_pr_build_failure", "ci_fix_proposal": {"files": ["x"]}}
    assert _route_after_ci_fixer_single_agent(state) == "pr_fix_commenter"


def test_route_after_ci_fixer_single_agent_routes_to_pr_creator_when_files_present() -> None:
    state = {"event_kind": "github_ci_failure", "ci_fix_proposal": {"files": ["x"]}}
    assert _route_after_ci_fixer_single_agent(state) == "pr_creator"


def test_route_after_ci_fixer_single_agent_routes_to_finalizer_when_no_fix_files() -> None:
    state = {"event_kind": "github_ci_failure", "ci_fix_proposal": {}}
    assert _route_after_ci_fixer_single_agent(state) == "finalizer"
