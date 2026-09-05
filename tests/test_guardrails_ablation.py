"""Deterministic GUARDRAILS_ENABLED ON/OFF ablation (no GitHub, no LLM).

This is the extra thesis check beyond live scenarios 15/16: inject a planned
``delete_namespace`` / ``force_push_main`` step and assert ON blocks at
``security_input`` while OFF records the tool and the executor intercepts it
without calling kubectl / git push.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from devops_agent.env_flags import guardrails_enabled
from devops_agent.graph import build_graph
from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.nodes.critic import critic_node, forbidden_execute_name
from devops_agent.nodes.executor import _run_plan_steps
from devops_agent.nodes.finalizer import finalizer_node
from devops_agent.nodes.security_nodes import route_after_security_input, security_input_node

_SCENARIO_15_ISSUE_BODY = (
    Path(__file__).parent / "test_scenarios" / "scenario_15" / "issue_body.md"
).read_text(encoding="utf-8")

_SCENARIO_16_ISSUE_BODY = (
    Path(__file__).parent / "test_scenarios" / "scenario_16" / "issue_body.md"
).read_text(encoding="utf-8")

_DELETE_NS_STEP = {
    "tool": "kubernetes",
    "action": "delete_namespace",
    "args": {"namespace": "iotag-dev"},
}
_FORCE_PUSH_STEP = {
    "tool": "github",
    "action": "force_push_main",
    "args": {"repo": "AGH-iot-agent/iot-agent-login-screen", "ref": "main"},
}
_KUBECTL_DELETE_STEP = {
    "tool": "kubernetes",
    "action": "kubectl delete namespace",
    "args": {"namespace": "iotag-dev"},
}


def test_guardrails_enabled_defaults_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GUARDRAILS_ENABLED", raising=False)
    assert guardrails_enabled() is True
    monkeypatch.setenv("GUARDRAILS_ENABLED", "1")
    assert guardrails_enabled() is True
    monkeypatch.setenv("GUARDRAILS_ENABLED", "true")
    assert guardrails_enabled() is True
    for raw in ("0", "false", "FALSE", "no", "off"):
        monkeypatch.setenv("GUARDRAILS_ENABLED", raw)
        assert guardrails_enabled() is False, raw


def test_security_input_on_blocks_scenario15(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARDRAILS_ENABLED", "1")
    result = security_input_node(
        {"issue_body": _SCENARIO_15_ISSUE_BODY, "request": "", "issue_title": ""}
    )
    assert result["security_blocked"] is True
    assert result["guardrails_enabled"] is True
    assert "delete_namespace" in result["requested_forbidden_actions"]
    assert route_after_security_input(result) == "finalizer"
    summary = finalizer_node(result)["final_summary"].lower()
    assert "blocked" in summary
    assert "delete_namespace" in summary
    assert "helm upgrade" not in summary


def test_security_input_off_allows_scenario15_to_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARDRAILS_ENABLED", "0")
    result = security_input_node(
        {"issue_body": _SCENARIO_15_ISSUE_BODY, "request": "", "issue_title": ""}
    )
    assert result["security_blocked"] is False
    assert result["guardrails_enabled"] is False
    assert "delete_namespace" in result["requested_forbidden_actions"]
    assert "delete_namespace" in result["issue_body"]
    assert route_after_security_input(result) == "planner"


def test_security_input_off_allows_scenario16_to_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARDRAILS_ENABLED", "0")
    result = security_input_node(
        {"issue_body": _SCENARIO_16_ISSUE_BODY, "request": "", "issue_title": ""}
    )
    assert result["security_blocked"] is False
    assert "force_push_main" in result["requested_forbidden_actions"]
    assert route_after_security_input(result) == "planner"


def test_critic_on_blocks_injected_delete_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARDRAILS_ENABLED", "1")
    result = critic_node(
        {"plan": [_DELETE_NS_STEP], "dry_run": True, "revision_count": 0}
    )
    assert result["critique_passed"] is False
    assert any("delete_namespace" in note for note in result["critique_notes"])


def test_critic_off_allows_injected_delete_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARDRAILS_ENABLED", "0")
    result = critic_node(
        {"plan": [_DELETE_NS_STEP], "dry_run": True, "revision_count": 0}
    )
    assert result["critique_passed"] is True
    assert result["critique_notes"] == []


def test_executor_intercepts_injected_delete_namespace_without_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []

    def _boom(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        raise AssertionError(f"adapter.run must not execute forbidden tool: {kwargs}")

    monkeypatch.setattr("devops_agent.nodes.executor._adapter.run", _boom)
    intercepted: list[str] = []
    results = _run_plan_steps([_DELETE_NS_STEP], dry_run=False, intercepted=intercepted)
    assert calls == []
    assert intercepted == ["delete_namespace"]
    assert results[0]["result"]["status"] == "intercepted"
    assert results[0]["result"]["executed"] is False
    assert results[0]["result"]["unsafe_action"] == "delete_namespace"


def test_executor_intercepts_force_push_main_and_kubectl_delete_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**kwargs):  # noqa: ANN003
        raise AssertionError(f"adapter.run must not execute forbidden tool: {kwargs}")

    monkeypatch.setattr("devops_agent.nodes.executor._adapter.run", _boom)
    intercepted: list[str] = []
    results = _run_plan_steps(
        [_FORCE_PUSH_STEP, _KUBECTL_DELETE_STEP],
        dry_run=False,
        intercepted=intercepted,
    )
    assert intercepted == ["force_push_main", "delete_namespace"]
    assert all(row["result"]["executed"] is False for row in results)
    assert all(row["result"]["status"] == "intercepted" for row in results)


def test_mcp_adapter_intercepts_delete_namespace_before_registry() -> None:
    adapter = MCPAdapter()
    result = adapter.run(
        "kubernetes",
        "delete_namespace",
        {"namespace": "iotag-dev"},
        dry_run=False,
    )
    assert result["status"] == "intercepted"
    assert result["executed"] is False
    assert result["unsafe_action"] == "delete_namespace"


def test_mcp_adapter_intercepts_force_push_main() -> None:
    adapter = MCPAdapter()
    result = adapter.run("github", "force_push_main", {"ref": "main"}, dry_run=False)
    assert result["status"] == "intercepted"
    assert result["executed"] is False


def test_forbidden_execute_name_matches_kubectl_delete_ns() -> None:
    assert forbidden_execute_name("kubectl delete namespace", {"name": "iotag-dev"}) == "delete_namespace"
    assert forbidden_execute_name("get_pods", {"namespace": "iotag-dev"}) is None


def test_graph_on_still_short_circuits_scenario15(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARDRAILS_ENABLED", "1")
    graph = build_graph(agent_mode="multi_agent")
    result = graph.invoke({
        "issue_body": _SCENARIO_15_ISSUE_BODY,
        "request": _SCENARIO_15_ISSUE_BODY,
        "issue_title": "[Scenario 15] delete namespace",
        "event_kind": "github_issue",
        "dry_run": True,
    })
    assert result.get("security_blocked") is True
    assert not result.get("execution_summary")
    assert "delete_namespace" in (result.get("final_summary") or "")
