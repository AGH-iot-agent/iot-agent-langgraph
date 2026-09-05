from __future__ import annotations

from tests.test_scenarios.helm_fixtures import inject_scenario_02, set_replica_count
from tests.test_scenarios.helm_repos import (
    SCENARIO_TARGET_REPO,
    helm_repo_basenames,
    parse_repo_filter,
    marker_for_repo,
    scenario_allowed,
    scenario_id_from_nodeid,
)


def test_discover_helm_repos_includes_login_screen_and_auth() -> None:
    names = helm_repo_basenames()
    assert "iot-agent-login-screen" in names
    assert "iot-agent-authentication" in names
    assert "iot-agent-gateway-api" in names
    assert "iot-agent-ops" not in names
    assert "iot-agent-monitoring" not in names


def test_parse_repo_filter_helm_excludes_repos_without_values(monkeypatch) -> None:
    monkeypatch.setenv("TEST_SCENARIO_REPOS", "helm")
    selected = parse_repo_filter()
    assert selected is not None
    assert "iot-agent-login-screen" in selected
    assert scenario_allowed("02", selected) is True
    assert scenario_allowed("04", selected) is True
    # logs / alert-api are not local helm siblings
    if "iot-agent-logs" not in selected:
        assert scenario_allowed("06", selected) is False
        assert scenario_allowed("10", selected) is False


def test_parse_repo_filter_all_is_unfiltered(monkeypatch) -> None:
    monkeypatch.setenv("TEST_SCENARIO_REPOS", "all")
    assert parse_repo_filter() is None
    assert scenario_allowed("10", None) is True


def test_parse_repo_filter_short_name(monkeypatch) -> None:
    monkeypatch.setenv("TEST_SCENARIO_REPOS", "login-screen")
    selected = parse_repo_filter()
    assert selected == {"iot-agent-login-screen"}
    assert scenario_allowed("02", selected) is True
    assert scenario_allowed("03", selected) is False


def test_scenario_id_and_marker() -> None:
    assert scenario_id_from_nodeid("tests/test_scenarios/scenario_02/scenario_02.py::test_x") == "02"
    assert marker_for_repo("iot-agent-login-screen") == "repo_login_screen"
    assert SCENARIO_TARGET_REPO["15"] == "iot-agent-login-screen"


def test_inject_scenario_02_keeps_real_file_shape() -> None:
    base = (
        "name: iot-agent-login-screen\n"
        "replicaCount: 1\n"
        "image:\n"
        "  tag: ''\n"
        "istio:\n"
        "  virtualService:\n"
        "    enabled: true\n"
        "service:\n"
        "  port: 80\n"
        "livenessProbe:\n"
        "  enabled: true\n"
        "  path: /\n"
        "readinessProbe:\n"
        "  path: /\n"
    )
    mutated = inject_scenario_02(base)
    assert 'replicaCount: "not-a-number"' in mutated
    assert "path: /invalid-path-for-liveness-probe" in mutated
    assert "readinessProbe:\n  path: /" in mutated
    assert "name: iot-agent-login-screen" in mutated
    restored = set_replica_count(mutated, "1")
    assert "replicaCount: 1\n" in restored
    assert '"not-a-number"' not in restored
