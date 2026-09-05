from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from devops_agent.mcp_adapter import MCPAdapter

_helper_path = Path(__file__).resolve().parents[3] / "tests" / "test_scenarios" / "test_scenarios_e2e.py"
_spec = importlib.util.spec_from_file_location("_scenario_helpers", _helper_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load helper module from {_helper_path}")
_scenario_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_scenario_helpers)

MONITOR_SCENARIO_SPECS = _scenario_helpers.MONITOR_SCENARIO_SPECS
_assert_monitor_scenario_alert = _scenario_helpers._assert_monitor_scenario_alert
real_github_adapter = _scenario_helpers.real_github_adapter
scenario_wait_settings = _scenario_helpers.scenario_wait_settings
e2e_preflight = _scenario_helpers.e2e_preflight


@pytest.mark.integration
def test_scenario13_emits_namespace_quota_exceeded_alert(
    e2e_preflight: None,
    real_github_adapter: MCPAdapter,
    scenario_wait_settings: dict[str, int],
    agent_mode: str,
) -> None:
    result = _assert_monitor_scenario_alert(
        "13",
        MONITOR_SCENARIO_SPECS["13"],
        real_github_adapter,
        scenario_wait_settings,
        agent_mode=agent_mode,
    )
    matched_alert = dict(result["matched_alert"])
    assert matched_alert.get("kind") == "namespace_quota_exceeded"
