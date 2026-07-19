from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_script_module() -> ModuleType:
    script_path = Path(__file__).with_name("generate_live_fixture.py")
    spec = importlib.util.spec_from_file_location("generate_live_fixture_under_test", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_writes_fixture_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script_module()
    out_path = tmp_path / "pr71_execution_results.json"
    monkeypatch.setattr(module, "_OUT_PATH", out_path)

    class _FakeGHAdapter:
        def get_pull_request(self, repo: str, pr_number: int) -> dict[str, Any]:
            assert repo == module._TARGET_REPO
            assert pr_number == module._PR_NUMBER
            return {
                "status": "ok",
                "pull_request": {
                    "head": {"ref": "test/generated-branch"}
                },
            }

    def _fake_planner_node(state: dict[str, Any]) -> dict[str, Any]:
        assert state["event_kind"] == "github_ci_failure"
        assert state["context"]["repo_full_name"] == module._TARGET_REPO
        assert state["context"]["run_id"] == module._REAL_RUN_ID
        state["plan"] = [{"step": "collect logs"}]
        return state

    def _fake_executor_node(state: dict[str, Any]) -> dict[str, Any]:
        assert state.get("plan")
        state["execution_results"] = [{"step": 1, "tool": "github", "status": "ok"}]
        return state

    monkeypatch.setattr(module, "GHAdapter", _FakeGHAdapter)
    monkeypatch.setattr(module, "planner_node", _fake_planner_node)
    monkeypatch.setattr(module, "executor_node", _fake_executor_node)

    module.main()

    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload == [{"step": 1, "tool": "github", "status": "ok"}]


def test_main_exits_when_pr_fetch_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script_module()
    out_path = tmp_path / "pr71_execution_results.json"
    monkeypatch.setattr(module, "_OUT_PATH", out_path)

    class _FailingGHAdapter:
        def get_pull_request(self, repo: str, pr_number: int) -> dict[str, Any]:
            assert repo == module._TARGET_REPO
            assert pr_number == module._PR_NUMBER
            return {"status": "error", "message": "not found"}

    monkeypatch.setattr(module, "GHAdapter", _FailingGHAdapter)

    with pytest.raises(SystemExit, match="Could not fetch PR #71"):
        module.main()

    assert not out_path.exists()


def test_main_exits_when_planner_returns_empty_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script_module()
    out_path = tmp_path / "pr71_execution_results.json"
    monkeypatch.setattr(module, "_OUT_PATH", out_path)

    class _FakeGHAdapter:
        def get_pull_request(self, repo: str, pr_number: int) -> dict[str, Any]:
            return {
                "status": "ok",
                "pull_request": {
                    "head": {"ref": "test/generated-branch"}
                },
            }

    monkeypatch.setattr(module, "GHAdapter", _FakeGHAdapter)
    monkeypatch.setattr(module, "planner_node", lambda state: state)
    monkeypatch.setattr(module, "executor_node", lambda state: state)

    with pytest.raises(SystemExit, match="planner_node produced an empty plan"):
        module.main()

    assert not out_path.exists()


def test_main_exits_when_executor_returns_no_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_script_module()
    out_path = tmp_path / "pr71_execution_results.json"
    monkeypatch.setattr(module, "_OUT_PATH", out_path)

    class _FakeGHAdapter:
        def get_pull_request(self, repo: str, pr_number: int) -> dict[str, Any]:
            return {
                "status": "ok",
                "pull_request": {
                    "head": {"ref": "test/generated-branch"}
                },
            }

    def _planner_with_plan(state: dict[str, Any]) -> dict[str, Any]:
        state["plan"] = [{"step": "collect logs"}]
        return state

    def _executor_without_results(state: dict[str, Any]) -> dict[str, Any]:
        state["execution_results"] = []
        return state

    monkeypatch.setattr(module, "GHAdapter", _FakeGHAdapter)
    monkeypatch.setattr(module, "planner_node", _planner_with_plan)
    monkeypatch.setattr(module, "executor_node", _executor_without_results)

    with pytest.raises(SystemExit, match="executor_node returned no execution_results"):
        module.main()

    assert not out_path.exists()