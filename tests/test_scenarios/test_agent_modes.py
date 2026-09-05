from __future__ import annotations

from tests.test_scenarios.agent_modes import apply_agent_mode, apply_guardrails_enabled, scenario_agent_modes
from tests.test_scenarios.env_sanitize import sanitize_scenario_environ


def test_scenario_agent_modes_default_is_both(monkeypatch) -> None:
    monkeypatch.delenv("SCENARIO_AGENT_MODES", raising=False)
    assert scenario_agent_modes() == ["multi_agent", "single_agent"]


def test_scenario_agent_modes_one_mode_for_debug(monkeypatch) -> None:
    monkeypatch.setenv("SCENARIO_AGENT_MODES", "single_agent")
    assert scenario_agent_modes() == ["single_agent"]
    monkeypatch.setenv("SCENARIO_AGENT_MODES", "multi_agent, bogus, single_agent, multi_agent")
    assert scenario_agent_modes() == ["multi_agent", "single_agent"]


def test_apply_agent_mode_sets_environ(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_MODE", raising=False)
    assert apply_agent_mode("single_agent") == "single_agent"
    import os

    assert os.environ["AGENT_MODE"] == "single_agent"


def test_apply_guardrails_enabled_sets_environ(monkeypatch) -> None:
    apply_guardrails_enabled(False)
    import os

    assert os.environ["GUARDRAILS_ENABLED"] == "0"
    apply_guardrails_enabled(True)
    assert os.environ["GUARDRAILS_ENABLED"] == "1"


def test_sanitize_strips_quoted_namespace_and_aliases_tokens() -> None:
    env = {
        "TEST_CI_FIX_TARGET_NAMESPACE": '"iotag-sbx"',
        "TEST_CI_FIX_KUBECONFIG": '"/etc/rancher/k3s/k3s.yaml"',
        "GH_TOKEN": "ghp_bot",
        "KUBECONFIG": "",
    }
    sanitize_scenario_environ(env)
    assert env["TEST_CI_FIX_TARGET_NAMESPACE"] == "iotag-sbx"
    assert env["TEST_CI_FIX_KUBECONFIG"] == "/etc/rancher/k3s/k3s.yaml"
    assert env["GITHUB_TOKEN"] == "ghp_bot"
    assert env["KUBECONFIG"] == "/etc/rancher/k3s/k3s.yaml"
    assert env["GH_TOKEN"] == "ghp_bot"
