from __future__ import annotations

from devops_agent.promptops.experiment_metrics import (
    ExperimentRun,
    aggregate_variant_metrics,
    load_experiment_csv,
    write_experiment_csv,
    write_metrics_csv,
)


def _run(**overrides: object) -> ExperimentRun:
    values: dict[str, object] = {
        "scenario_id": "scenario_02",
        "trial_id": 1,
        "variant": "single_agent",
        "trace_id": "trace-1",
        "diagnosis_correct": True,
        "fix_correct": True,
        "fix_is_valid": True,
        "validation_passed": True,
        "policy_compliant": True,
        "unsafe_action_requested": False,
        "unsafe_action_blocked": False,
        "safe_action_blocked": False,
        "mttr_s": 10.0,
        "total_tokens": 100,
    }
    values.update(overrides)
    return ExperimentRun(**values)  # type: ignore[arg-type]


def test_aggregates_diagnosis_workflow_sandbox_and_safety_metrics() -> None:
    runs = [
        _run(),
        _run(
            scenario_id="scenario_04",
            trial_id=2,
            trace_id="trace-2",
            diagnosis_correct=False,
            fix_correct=False,
            fix_is_valid=False,
            validation_passed=False,
            unsafe_action_requested=True,
            unsafe_action_blocked=True,
            mttr_s=30.0,
            total_tokens=300,
        ),
    ]

    metrics = aggregate_variant_metrics(runs)

    assert len(metrics) == 1
    metric = metrics[0]
    assert metric.diagnosis_accuracy == 0.5
    assert metric.fix_accuracy == 0.5
    assert metric.workflow_success_rate == 0.5
    assert metric.unsafe_action_block_rate == 1.0
    assert metric.safe_action_false_block_rate == 0.0
    assert metric.valid_fix_acceptance_rate == 1.0
    assert metric.invalid_fix_rejection_rate == 1.0
    assert metric.avg_mttr_s == 20.0
    assert metric.avg_total_tokens == 200.0


def test_writes_raw_and_aggregated_csv_files(tmp_path) -> None:
    runs = [_run()]
    metrics = aggregate_variant_metrics(runs)
    raw_path = tmp_path / "runs.csv"
    metrics_path = tmp_path / "metrics.csv"

    write_experiment_csv(runs, raw_path)
    write_metrics_csv(metrics, metrics_path)

    assert "diagnosis_correct" in raw_path.read_text(encoding="utf-8")
    assert "workflow_success_rate" in metrics_path.read_text(encoding="utf-8")


def test_loads_raw_csv_and_rejects_duplicate_trial(tmp_path) -> None:
    path = tmp_path / "runs.csv"
    write_experiment_csv([_run()], path)

    assert load_experiment_csv(path) == [_run()]

    with path.open("a", encoding="utf-8") as output:
        output.write(
            "scenario_02,1,single_agent,trace-2,True,True,True,True,True,False,False,False,10.0,100\n"
        )

    try:
        load_experiment_csv(path)
    except ValueError as exc:
        assert "Powielony przebieg" in str(exc)
    else:
        raise AssertionError("Oczekiwano odrzucenia powielonego przebiegu")