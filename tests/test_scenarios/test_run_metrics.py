from __future__ import annotations

from pathlib import Path

from tests.test_scenarios.run_metrics import (
    classify_failure,
    record_run,
    reset_for_tests,
    restore_runs,
    write_reports,
)


def test_write_reports_distinguishes_llm_and_workflow(tmp_path: Path) -> None:
    previous = reset_for_tests()
    try:
        record_run(
            scenario_id="01",
            agent_mode="multi_agent",
            duration_s=12.5,
            workflow_ok=True,
            scored={"diagnosis_correct": True, "fix_correct": False},
            telemetry={"revision_count": 1, "total_tokens": 100, "validation_passed": False, "trace_id": "t1"},
            nodeid="test_scenario01_example",
        )
        path = write_reports(tmp_path)
        assert path is not None
        text = path.read_text(encoding="utf-8")
        assert "workflow_ok" in text
        assert "llm_success" in text
        assert "Scenario × agent_mode" in text
        assert "multi_ok" in text
        assert (tmp_path / "latest.json").exists()
        assert (tmp_path / "latest_runs.csv").exists()
    finally:
        restore_runs(previous)


def test_write_reports_always_writes_even_when_empty(tmp_path: Path) -> None:
    previous = reset_for_tests()
    try:
        path = write_reports(tmp_path)
        assert path.exists()
        payload = path.read_text(encoding="utf-8")
        assert "runs: 0" in payload
        assert (tmp_path / "latest.json").exists()
    finally:
        restore_runs(previous)


def test_classify_failure_taxonomy_and_thesis_fields(tmp_path: Path) -> None:
    assert classify_failure("original_snippet not found in base", None) == "patchability_loop"
    assert classify_failure(None, "Spring Boot 256Mi recipe") == "premature_oom_guess"
    previous = reset_for_tests()
    try:
        record_run(
            scenario_id="02",
            agent_mode="single_agent",
            duration_s=10,
            workflow_ok=False,
            comment_body="--- a/Helm/values-sbx.yaml+++ b/Helm/values-sbx.yaml",
            scored={"diagnosis_correct": False, "missing_root_cause_indicators": ["liveness"]},
            error="original_snippet not found in base branch content",
            nodeid="s02-single",
            live=True,
        )
        path = write_reports(tmp_path)
        text = path.read_text(encoding="utf-8")
        assert "Promotor questions" in text
        assert "trial_agreement_rate" in text
        payload = (tmp_path / "latest.json").read_text(encoding="utf-8")
        assert "patchability_loop" in payload
        assert "self_repair_workflow" in payload
    finally:
        restore_runs(previous)
