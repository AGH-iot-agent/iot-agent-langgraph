#!/usr/bin/env python3
"""CLI do zarządzania PromptOps – ewaluacje, benchmarki, regresje, datasety.

Użycie:
    python -m devops_agent.promptops.cli eval      --variant ci_fixer_v1 --dataset github_ci_failure
    python -m devops_agent.promptops.cli ab        --dataset github_ci_failure --variant-a ci_fixer_v1 --variant-b ci_fixer_v2
    python -m devops_agent.promptops.cli benchmark --dataset github_ci_failure --variant ci_fixer_v2 --models gpt-4o-mini gpt-4o
    python -m devops_agent.promptops.cli regression --baseline-run <run_id> --candidate-run <run_id>
    python -m devops_agent.promptops.cli list-runs  [--variant ci_fixer_v1] [--dataset github_ci_failure]
    python -m devops_agent.promptops.cli list-variants
    python -m devops_agent.promptops.cli dataset-stats
    python -m devops_agent.promptops.cli add-case   --dataset github_ci_failure --file case.json
    python -m devops_agent.promptops.cli ab-stats
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("promptops.cli")


def cmd_eval(args: argparse.Namespace) -> None:
    from devops_agent.promptops.evaluator import EvalRunner
    runner = EvalRunner()
    run = runner.run(
        variant_id=args.variant,
        event_kind=args.dataset,
        model=args.model,
        use_llm_judge=args.llm_judge,
        max_cases=args.max_cases,
    )
    _print_run_summary(run)


def cmd_ab(args: argparse.Namespace) -> None:
    """Uruchamia ewaluację dwóch wariantów i porównuje wyniki."""
    from devops_agent.promptops.evaluator import EvalRunner
    from devops_agent.promptops.regression import RegressionDetector

    runner = EvalRunner()
    print(f"\n[A] Ewaluacja wariantu: {args.variant_a}")
    run_a = runner.run(
        variant_id=args.variant_a,
        event_kind=args.dataset,
        model=args.model,
        use_llm_judge=args.llm_judge,
        max_cases=args.max_cases,
    )
    print(f"\n[B] Ewaluacja wariantu: {args.variant_b}")
    run_b = runner.run(
        variant_id=args.variant_b,
        event_kind=args.dataset,
        model=args.model,
        use_llm_judge=args.llm_judge,
        max_cases=args.max_cases,
    )

    detector = RegressionDetector(threshold=args.threshold)
    # A jest baseline, B jest kandydatem
    report = detector.compare(baseline=run_a, candidate=run_b)
    _print_run_summary(run_a, label="[A] BASELINE")
    _print_run_summary(run_b, label="[B] KANDYDAT")
    print("\n=== PORÓWNANIE A/B ===")
    print(f"  Delta pass_rate : {report.delta:+.1%}")
    print(f"  Regresja        : {'TAK ⚠' if report.regression_detected else 'NIE ✓'}")
    for note in report.notes:
        print(f"  {note}")


def cmd_benchmark(args: argparse.Namespace) -> None:
    from devops_agent.promptops.benchmarks import ModelBenchmark
    bench = ModelBenchmark()
    report = bench.run(
        models=args.models,
        variant_id=args.variant,
        event_kind=args.dataset,
        use_llm_judge=args.llm_judge,
        max_cases=args.max_cases,
    )
    print("\n=== BENCHMARK LEADERBOARD ===")
    print(f"  {'MODEL':<22} {'PASS%':<8} {'AVG_SCORE':<11} {'LATENCY(ms)':<13} {'AVG_TOK'}")
    print("  " + "-" * 68)
    for e in report.entries:
        print(
            f"  {e.model:<22} {e.pass_rate:.1%}    {e.avg_score:.3f}      "
            f"{e.avg_latency_ms:<13.0f}  {e.avg_tokens:.0f}"
        )
    if report.best_model:
        print(f"\n  Najlepszy model: {report.best_model}")


def cmd_regression(args: argparse.Namespace) -> None:
    from devops_agent.promptops.evaluator import EvalRunner
    from devops_agent.promptops.regression import RegressionDetector

    runner = EvalRunner()
    baseline = runner.load_run(args.baseline_run)
    candidate = runner.load_run(args.candidate_run)
    detector = RegressionDetector(threshold=args.threshold)
    report = detector.compare(baseline, candidate)

    print(f"\nBaseline : {baseline.variant_id} @ {baseline.model} (run={baseline.run_id})")
    print(f"Kandydat : {candidate.variant_id} @ {candidate.model} (run={candidate.run_id})")
    print(f"Delta    : {report.delta:+.1%}")
    print(f"Regresja : {'TAK ⚠' if report.regression_detected else 'NIE ✓'}")
    for note in report.notes:
        print(f"  {note}")

    if args.json:
        print(report.model_dump_json(indent=2))


def cmd_list_runs(args: argparse.Namespace) -> None:
    from devops_agent.promptops.evaluator import EvalRunner
    runner = EvalRunner()
    runs = runner.list_runs(
        variant_id=args.variant,
        event_kind=args.dataset,
        model=args.model,
    )
    if not runs:
        print("Brak przebiegów.")
        return
    print(f"\n{'RUN_ID':<14} {'VARIANT':<22} {'MODEL':<20} {'DATASET':<25} {'PASS%':<8} {'DATE'}")
    print("-" * 100)
    for r in sorted(runs, key=lambda x: x.timestamp, reverse=True):
        print(
            f"{r.run_id:<14} {r.variant_id:<22} {r.model:<20} {r.event_kind:<25} "
            f"{r.pass_rate:.1%}    {r.timestamp.strftime('%Y-%m-%d %H:%M')}"
        )


def cmd_list_variants(args: argparse.Namespace) -> None:
    from devops_agent.promptops.registry import get_registry
    registry = get_registry()
    variants = registry.list_variants(args.name or None)
    if not variants:
        print("Brak zarejestrowanych wariantów.")
        return
    print(f"\n{'VARIANT_ID':<22} {'NAME':<15} {'WEIGHT':<8} {'ACTIVE':<8} DESCRIPTION")
    print("-" * 80)
    for v in sorted(variants, key=lambda x: (x.name, x.variant_id)):
        print(f"{v.variant_id:<22} {v.name:<15} {v.weight:<8.1f} {str(v.active):<8} {v.description}")


def cmd_dataset_stats(args: argparse.Namespace) -> None:
    from devops_agent.promptops.datasets import get_dataset_manager
    mgr = get_dataset_manager()
    stats = mgr.stats()
    if not stats:
        print("Brak datasetów.")
        return
    print("\n=== DATASETY ===")
    for ek, count in sorted(stats.items()):
        print(f"  {ek:<35} {count} przypadków")


def cmd_add_case(args: argparse.Namespace) -> None:
    from devops_agent.promptops.datasets import get_dataset_manager
    from devops_agent.promptops.models import EvalCase
    mgr = get_dataset_manager()
    raw = Path(args.file).read_text(encoding="utf-8")
    data = json.loads(raw)
    data["event_kind"] = args.dataset
    case = EvalCase.model_validate(data)
    mgr.add_case(case)
    print(f"Dodano case '{case.case_id}' do datasetu '{args.dataset}'.")


def cmd_ab_stats(args: argparse.Namespace) -> None:
    from devops_agent.promptops.registry import get_registry
    registry = get_registry()
    stats = registry.ab_stats()
    if not stats:
        print("Brak danych A/B.")
        return
    print("\n=== STATYSTYKI A/B ===")
    for name, counts in sorted(stats.items()):
        total = sum(counts.values())
        print(f"\n  {name} (łącznie: {total} przypisań):")
        for vid, cnt in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"    {vid}: {cnt} ({cnt/total:.1%})")


def _print_run_summary(run: object, label: str = "WYNIKI") -> None:
    print(f"\n=== {label} ===")
    print(f"  run_id     : {run.run_id}")
    print(f"  variant    : {run.variant_id}")
    print(f"  model      : {run.model}")
    print(f"  dataset    : {run.event_kind}")
    print(f"  pass_rate  : {run.pass_rate:.1%}  ({run.passed}/{run.total_cases} pass, {run.partial} partial, {run.failed} fail)")
    print(f"  avg_score  : {run.avg_score:.3f}")
    print(f"  avg_latency: {run.avg_latency_ms:.0f}ms")
    print(f"  avg_tokens : {run.avg_tokens:.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PromptOps CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ---- eval ----
    p_eval = sub.add_parser("eval", help="Uruchom ewaluację wariantu")
    p_eval.add_argument("--variant", required=True)
    p_eval.add_argument("--dataset", required=True)
    p_eval.add_argument("--model", default=None)
    p_eval.add_argument("--llm-judge", action="store_true")
    p_eval.add_argument("--max-cases", type=int, default=None)

    # ---- ab ----
    p_ab = sub.add_parser("ab", help="A/B test dwóch wariantów")
    p_ab.add_argument("--dataset", required=True)
    p_ab.add_argument("--variant-a", required=True)
    p_ab.add_argument("--variant-b", required=True)
    p_ab.add_argument("--model", default=None)
    p_ab.add_argument("--llm-judge", action="store_true")
    p_ab.add_argument("--max-cases", type=int, default=None)
    p_ab.add_argument("--threshold", type=float, default=0.05)

    # ---- benchmark ----
    p_bench = sub.add_parser("benchmark", help="Benchmarkuj wiele modeli")
    p_bench.add_argument("--dataset", required=True)
    p_bench.add_argument("--variant", required=True)
    p_bench.add_argument("--models", nargs="+", required=True)
    p_bench.add_argument("--llm-judge", action="store_true")
    p_bench.add_argument("--max-cases", type=int, default=None)

    # ---- regression ----
    p_reg = sub.add_parser("regression", help="Sprawdź regresję między dwoma runs")
    p_reg.add_argument("--baseline-run", required=True)
    p_reg.add_argument("--candidate-run", required=True)
    p_reg.add_argument("--threshold", type=float, default=0.05)
    p_reg.add_argument("--json", action="store_true")

    # ---- list-runs ----
    p_lr = sub.add_parser("list-runs", help="Lista przebiegów ewaluacji")
    p_lr.add_argument("--variant", default=None)
    p_lr.add_argument("--dataset", default=None)
    p_lr.add_argument("--model", default=None)

    # ---- list-variants ----
    p_lv = sub.add_parser("list-variants", help="Lista wariantów w rejestrze")
    p_lv.add_argument("--name", default=None)

    # ---- dataset-stats ----
    sub.add_parser("dataset-stats", help="Statystyki datasetów")

    # ---- add-case ----
    p_ac = sub.add_parser("add-case", help="Dodaj przypadek testowy do datasetu")
    p_ac.add_argument("--dataset", required=True)
    p_ac.add_argument("--file", required=True, help="Ścieżka do pliku JSON z EvalCase")

    # ---- ab-stats ----
    sub.add_parser("ab-stats", help="Statystyki przypisań A/B")

    args = parser.parse_args()

    dispatch = {
        "eval": cmd_eval,
        "ab": cmd_ab,
        "benchmark": cmd_benchmark,
        "regression": cmd_regression,
        "list-runs": cmd_list_runs,
        "list-variants": cmd_list_variants,
        "dataset-stats": cmd_dataset_stats,
        "add-case": cmd_add_case,
        "ab-stats": cmd_ab_stats,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
