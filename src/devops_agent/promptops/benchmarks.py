"""Benchmarking modeli – porównanie wielu modeli na tym samym datasecie i wariancie prompta."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from devops_agent.promptops.evaluator import EvalRunner
from devops_agent.promptops.models import BenchmarkEntry, BenchmarkReport, EvalRun

logger = logging.getLogger(__name__)


class ModelBenchmark:
    """Uruchamia ewaluację dla wielu modeli równolegle i zestawia wyniki."""

    def __init__(self, runner: EvalRunner | None = None) -> None:
        self._runner = runner or EvalRunner()

    def run(
        self,
        models: list[str],
        variant_id: str,
        event_kind: str,
        max_workers: int = 3,
        use_llm_judge: bool = False,
        max_cases: int | None = None,
    ) -> BenchmarkReport:
        """Benchmarkuje listę modeli.

        Args:
            models: np. ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"].
            variant_id: ID wariantu prompta.
            event_kind: Typ zdarzenia / nazwa datasetu.
            max_workers: Ile modeli testować równolegle.
            use_llm_judge: Czy używać sędziego LLM.
            max_cases: Limit przypadków (do smoke-testów).

        Returns:
            BenchmarkReport z posortowanymi wynikami.
        """
        report = BenchmarkReport(event_kind=event_kind, variant_id=variant_id)
        runs: dict[str, EvalRun] = {}

        logger.info(
            "Benchmark start: %d modeli, variant=%s, dataset=%s",
            len(models), variant_id, event_kind,
        )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    self._runner.run,
                    variant_id=variant_id,
                    event_kind=event_kind,
                    model=model,
                    use_llm_judge=use_llm_judge,
                    max_cases=max_cases,
                ): model
                for model in models
            }
            for future in as_completed(futures):
                model = futures[future]
                try:
                    run = future.result()
                    runs[model] = run
                    logger.info(
                        "  %s: pass_rate=%.1f%% avg_score=%.2f avg_latency=%.0fms",
                        model, run.pass_rate * 100, run.avg_score, run.avg_latency_ms,
                    )
                except Exception as exc:
                    logger.error("Błąd benchmarku dla modelu %s: %s", model, exc)

        # Zbuduj wpisy raportu
        for model, run in runs.items():
            report.entries.append(
                BenchmarkEntry(
                    model=model,
                    variant_id=variant_id,
                    pass_rate=run.pass_rate,
                    avg_score=run.avg_score,
                    avg_latency_ms=run.avg_latency_ms,
                    avg_tokens=run.avg_tokens,
                    run_id=run.run_id,
                )
            )

        # Sortuj malejąco po pass_rate, potem avg_score
        report.entries.sort(key=lambda e: (-e.pass_rate, -e.avg_score))

        self._print_leaderboard(report)
        return report

    def _print_leaderboard(self, report: BenchmarkReport) -> None:
        """Drukuje tabelkę wyników do logów."""
        logger.info("=== BENCHMARK LEADERBOARD ===")
        logger.info(
            "  %-20s  %-10s  %-10s  %-12s  %-10s",
            "MODEL", "PASS%", "AVG_SCORE", "LATENCY(ms)", "AVG_TOK",
        )
        logger.info("  " + "-" * 68)
        for e in report.entries:
            logger.info(
                "  %-20s  %-10s  %-10s  %-12s  %-10s",
                e.model,
                f"{e.pass_rate:.1%}",
                f"{e.avg_score:.3f}",
                f"{e.avg_latency_ms:.0f}",
                f"{e.avg_tokens:.0f}",
            )
        best = report.best_model
        if best:
            logger.info("  Najlepszy model: %s", best)

    def compare_variants(
        self,
        model: str,
        variant_ids: list[str],
        event_kind: str,
        max_cases: int | None = None,
    ) -> BenchmarkReport:
        """Porównuje warianty promptów na tym samym modelu.

        Przydatne do A/B testów wielu wariantów naraz zamiast w parach.
        """
        report = BenchmarkReport(event_kind=event_kind, variant_id="multi")
        logger.info(
            "Porównanie wariantów: model=%s, %d wariantów, dataset=%s",
            model, len(variant_ids), event_kind,
        )
        for vid in variant_ids:
            try:
                run = self._runner.run(
                    variant_id=vid,
                    event_kind=event_kind,
                    model=model,
                    max_cases=max_cases,
                )
                report.entries.append(
                    BenchmarkEntry(
                        model=model,
                        variant_id=vid,
                        pass_rate=run.pass_rate,
                        avg_score=run.avg_score,
                        avg_latency_ms=run.avg_latency_ms,
                        avg_tokens=run.avg_tokens,
                        run_id=run.run_id,
                    )
                )
                logger.info(
                    "  %s: pass_rate=%.1f%% avg_score=%.2f",
                    vid, run.pass_rate * 100, run.avg_score,
                )
            except Exception as exc:
                logger.error("Błąd ewaluacji wariantu %s: %s", vid, exc)

        report.entries.sort(key=lambda e: (-e.pass_rate, -e.avg_score))
        self._print_leaderboard(report)
        return report
