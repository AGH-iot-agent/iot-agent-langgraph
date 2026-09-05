from __future__ import annotations

from pathlib import Path

from tests.test_scenarios.conftest import (
    _LOGS_DIR,
    _REPO_ROOT,
    resolve_scenario_pytest_log_file,
)


def test_empty_and_repo_root_pytest_log_map_to_scenario_folder() -> None:
    assert resolve_scenario_pytest_log_file("") == _LOGS_DIR / "pytest.log"
    assert resolve_scenario_pytest_log_file("logs/pytest.log") == _LOGS_DIR / "pytest.log"
    assert resolve_scenario_pytest_log_file("logs\\pytest.log") == _LOGS_DIR / "pytest.log"
    assert resolve_scenario_pytest_log_file(str(_REPO_ROOT / "logs" / "pytest.log")) == (
        _LOGS_DIR / "pytest.log"
    )


def test_per_scenario_log_stays_in_folder() -> None:
    existing = _LOGS_DIR / "scenario_03.log"
    assert resolve_scenario_pytest_log_file(str(existing)) == existing
    assert resolve_scenario_pytest_log_file(
        "tests/test_scenarios/logs/scenario_01.log"
    ) == _LOGS_DIR / "scenario_01.log"


def test_all_scenarios_legacy_name_is_relocated() -> None:
    assert resolve_scenario_pytest_log_file("logs/all_scenarios.log") == (
        _LOGS_DIR / "all_scenarios.log"
    )


def test_tmp_pytest_log_is_pulled_into_scenario_folder(tmp_path: Path) -> None:
    assert resolve_scenario_pytest_log_file(str(tmp_path / "pytest.log")) == (
        _LOGS_DIR / "pytest.log"
    )
