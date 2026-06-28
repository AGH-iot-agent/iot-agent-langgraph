#!/usr/bin/env python3
"""run_model_benchmark.py – porównuje modele LLM na datasetach agenta.

Uruchamia ModelBenchmark dla podanych modeli i datasetów, zapisuje
BenchmarkReport do data/promptops/benchmark_results/.

Użycie:
    cd iot-agent-langgraph
    source .venv/bin/activate

    # Benchmark domyślny (wszystkie modele, wszystkie datasety)
    python scripts/run_model_benchmark.py

    # Wybrany dataset i modele
    python scripts/run_model_benchmark.py \\
        --datasets github_ci_failure github_issue prometheus_alert \\
        --models claude-3-haiku-20240307 claude-3-5-sonnet-20241022 gpt-4o-mini \\
        --variant ci_fixer_v1 \\
        --max-cases 5

    # A/B test wariantów prompta
    python scripts/run_model_benchmark.py \\
        --ab-variants ci_fixer_v1 ci_fixer_v2 \\
        --model claude-3-5-sonnet-20241022 \\
        --datasets github_ci_failure

Zmienne środowiskowe:
    ANTHROPIC_API_KEY   – klucz API Anthropic (dla modeli claude-*)
    OPENAI_API_KEY      – klucz API OpenAI (dla modeli gpt-*)
    DEVOPS_AGENT_MODEL  – domyślny model (fallback)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Dodaj src/ do PYTHONPATH
_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from devops_agent.promptops.benchmarks import ModelBenchmark
from devops_agent.promptops.evaluator import EvalRunner
from devops_agent.promptops.models import BenchmarkReport

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s – %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("benchmark")

RESULTS_DIR = Path(__file__).parent.parent / "data" / "promptops" / "benchmark_results"

DEFAULT_MODELS = [
    "claude-3-haiku-20240307",
    "claude-3-5-sonnet-20241022",
    "gpt-4o-mini",
]

DEFAULT_DATASETS = [
    "github_ci_failure",
    "github_issue",
    "prometheus_alert",
    "edge_cases",
]


def _save_report(report: BenchmarkReport, label: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"benchmark_{label}.json"
    out = RESULTS_DIR / fname
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Raport zapisany: %s", out)
    return out


def _print_table(report: BenchmarkReport) -> None:
    print(f"\n{'='*70}")
    print(f"BENCHMARK: event_kind={report.event_kind}  variant={report.variant_id}")
    print(f"{'='*70}")
    print(f"  {'MODEL':<30} {'PASS%':>7} {'AVG_SCORE':>10} {'LATENCY(ms)':>12} {'AVG_TOK':>9}")
    print(f"  {'-'*68}")
    for e in report.entries:
        print(
            f"  {e.model:<30} {e.pass_rate:>7.1%} {e.avg_score:>10.3f} "
            f"{e.avg_latency_ms:>12.0f} {e.avg_tokens:>9.0f}"
        )
    if report.best_model:
        print(f"\n  >> Najlepszy model: {report.best_model}")
    print()


def run_model_comparison(args: argparse.Namespace) -> None:
    runner = EvalRunner()
    benchmark = ModelBenchmark(runner=runner)

    all_reports: list[dict] = []

    for dataset in args.datasets:
        logger.info("=== Dataset: %s ===", dataset)
        try:
            report = benchmark.run(
                models=args.models,
                variant_id=args.variant,
                event_kind=dataset,
                max_workers=min(len(args.models), 3),
                use_llm_judge=args.llm_judge,
                max_cases=args.max_cases,
            )
            _print_table(report)
            path = _save_report(report, f"{dataset}_{args.variant}")
            all_reports.append({
                "dataset": dataset,
                "variant": args.variant,
                "best_model": report.best_model,
                "report_path": str(path),
            })
        except Exception as exc:
            logger.error("Błąd benchmarku dla datasetu %s: %s", dataset, exc)

    # Zbiorczy summary
    if all_reports:
        summary_path = RESULTS_DIR / "benchmark_summary.json"
        summary_path.write_text(json.dumps(all_reports, indent=2), encoding="utf-8")
        logger.info("Summary zapisany: %s", summary_path)


def run_ab_variants(args: argparse.Namespace) -> None:
    runner = EvalRunner()
    benchmark = ModelBenchmark(runner=runner)

    for dataset in args.datasets:
        logger.info("=== A/B warianty: dataset=%s model=%s ===", dataset, args.model)
        try:
            report = benchmark.compare_variants(
                model=args.model,
                variant_ids=args.ab_variants,
                event_kind=dataset,
                max_cases=args.max_cases,
            )
            _print_table(report)
            _save_report(report, f"{dataset}_ab_{args.model.replace('/', '-')}")
        except Exception as exc:
            logger.error("Błąd A/B dla datasetu %s: %s", dataset, exc)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark modeli LLM na datasetach agenta DevOps",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--models", nargs="+", default=DEFAULT_MODELS,
        help="Lista modeli do benchmarku (domyślnie: claude-3-haiku, claude-3-5-sonnet, gpt-4o-mini)",
    )
    parser.add_argument(
        "--datasets", nargs="+", default=DEFAULT_DATASETS,
        help="Datasety do testowania",
    )
    parser.add_argument(
        "--variant", default="ci_fixer_v1",
        help="ID wariantu prompta (domyślnie: ci_fixer_v1)",
    )
    parser.add_argument(
        "--max-cases", type=int, default=None,
        help="Limit przypadków per dataset (smoke test)",
    )
    parser.add_argument(
        "--llm-judge", action="store_true",
        help="Użyj sędziego LLM zamiast heurystyki (kosztowne)",
    )
    # A/B mode
    parser.add_argument(
        "--ab-variants", nargs="+", default=None,
        help="Tryb A/B: lista wariantów prompta do porównania",
    )
    parser.add_argument(
        "--model", default=os.getenv("DEVOPS_AGENT_MODEL", "claude-3-5-sonnet-20241022"),
        help="Model dla trybu A/B (jeden model)",
    )

    args = parser.parse_args()

    if args.ab_variants:
        run_ab_variants(args)
    else:
        run_model_comparison(args)


if __name__ == "__main__":
    main()
