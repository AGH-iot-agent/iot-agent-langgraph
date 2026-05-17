"""Detekcja regresji jakości między wersjami promptów.

Porównuje dwa przebiegi ewaluacji (baseline vs. candidate) i raportuje
czy nowy wariant jest gorszy o więcej niż zadany próg.
"""
from __future__ import annotations

import logging
from typing import Any

from devops_agent.promptops.models import EvalRun, RegressionReport

logger = logging.getLogger(__name__)

# Domyślny próg: jeśli pass_rate spada o więcej niż 5% → regresja
_DEFAULT_THRESHOLD = 0.05


class RegressionDetector:
    """Wykrywa regresje jakości między przebiegami ewaluacji."""

    def __init__(self, threshold: float = _DEFAULT_THRESHOLD) -> None:
        self.threshold = threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compare(self, baseline: EvalRun, candidate: EvalRun) -> RegressionReport:
        """Porównuje dwa przebiegi i zwraca raport regresji.

        Regresja jest wykryta jeśli:
            candidate.pass_rate - baseline.pass_rate < -threshold
        """
        if baseline.event_kind != candidate.event_kind:
            logger.warning(
                "Porównywane przebiegi mają różne event_kind: %s vs %s",
                baseline.event_kind, candidate.event_kind,
            )

        delta = candidate.pass_rate - baseline.pass_rate
        regression_detected = delta < -self.threshold

        per_case_delta = self._per_case_delta(baseline, candidate)

        notes = self._generate_notes(baseline, candidate, per_case_delta)

        report = RegressionReport(
            baseline_run_id=baseline.run_id,
            candidate_run_id=candidate.run_id,
            baseline_pass_rate=baseline.pass_rate,
            candidate_pass_rate=candidate.pass_rate,
            delta=round(delta, 4),
            regression_detected=regression_detected,
            threshold=self.threshold,
            per_case_delta=per_case_delta,
            notes=notes,
        )

        if regression_detected:
            logger.warning(
                "REGRESJA WYKRYTA: %s -> %s | pass_rate: %.1f%% -> %.1f%% (delta=%.1f%%)",
                baseline.variant_id, candidate.variant_id,
                baseline.pass_rate * 100, candidate.pass_rate * 100, delta * 100,
            )
        else:
            logger.info(
                "Brak regresji: %s -> %s | pass_rate: %.1f%% -> %.1f%% (delta=%.1f%%)",
                baseline.variant_id, candidate.variant_id,
                baseline.pass_rate * 100, candidate.pass_rate * 100, delta * 100,
            )

        return report

    def compare_latest(
        self,
        runs: list[EvalRun],
        variant_a: str,
        variant_b: str,
    ) -> RegressionReport:
        """Porównuje najnowsze przebiegi dwóch wariantów z listy."""
        def latest(vid: str) -> EvalRun:
            filtered = [r for r in runs if r.variant_id == vid]
            if not filtered:
                raise ValueError(f"Brak przebiegów dla wariantu '{vid}'")
            return max(filtered, key=lambda r: r.timestamp)

        return self.compare(latest(variant_a), latest(variant_b))

    def gate(
        self,
        baseline: EvalRun,
        candidate: EvalRun,
        min_pass_rate: float = 0.7,
    ) -> tuple[bool, RegressionReport]:
        """Quality gate – zwraca (True=OK, raport).

        Kandydat przechodzi jeśli:
        1. Brak regresji względem baseline.
        2. pass_rate >= min_pass_rate.
        """
        report = self.compare(baseline, candidate)
        meets_min = candidate.pass_rate >= min_pass_rate

        if not meets_min:
            report.notes.append(
                f"FAIL: pass_rate {candidate.pass_rate:.1%} < wymagane {min_pass_rate:.1%}"
            )

        ok = not report.regression_detected and meets_min
        return ok, report

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _per_case_delta(
        self, baseline: EvalRun, candidate: EvalRun
    ) -> dict[str, float]:
        """Oblicza deltę score dla każdego wspólnego case_id."""
        baseline_scores = {r.case_id: r.score for r in baseline.results}
        candidate_scores = {r.case_id: r.score for r in candidate.results}
        common = set(baseline_scores) & set(candidate_scores)
        return {
            case_id: round(candidate_scores[case_id] - baseline_scores[case_id], 4)
            for case_id in sorted(common)
        }

    def _generate_notes(
        self,
        baseline: EvalRun,
        candidate: EvalRun,
        per_case_delta: dict[str, float],
    ) -> list[str]:
        notes: list[str] = []

        notes.append(
            f"Baseline: {baseline.variant_id} @ {baseline.model} "
            f"(pass_rate={baseline.pass_rate:.1%}, avg_score={baseline.avg_score:.2f})"
        )
        notes.append(
            f"Kandydat: {candidate.variant_id} @ {candidate.model} "
            f"(pass_rate={candidate.pass_rate:.1%}, avg_score={candidate.avg_score:.2f})"
        )

        # Najgorsze regresje
        regressions = {k: v for k, v in per_case_delta.items() if v < -0.1}
        if regressions:
            worst = sorted(regressions.items(), key=lambda x: x[1])[:5]
            notes.append(
                "Największe regresje per-case: "
                + ", ".join(f"{cid}({v:+.2f})" for cid, v in worst)
            )

        # Poprawy
        improvements = {k: v for k, v in per_case_delta.items() if v > 0.1}
        if improvements:
            best = sorted(improvements.items(), key=lambda x: -x[1])[:5]
            notes.append(
                "Największe poprawy per-case: "
                + ", ".join(f"{cid}({v:+.2f})" for cid, v in best)
            )

        # Latencja
        lat_delta = candidate.avg_latency_ms - baseline.avg_latency_ms
        if abs(lat_delta) > 200:
            notes.append(
                f"Zmiana latencji: {lat_delta:+.0f}ms "
                f"({baseline.avg_latency_ms:.0f} -> {candidate.avg_latency_ms:.0f}ms)"
            )

        # Tokeny
        tok_delta = candidate.avg_tokens - baseline.avg_tokens
        if abs(tok_delta) > 100:
            notes.append(
                f"Zmiana tokenów: {tok_delta:+.0f} "
                f"({baseline.avg_tokens:.0f} -> {candidate.avg_tokens:.0f})"
            )

        return notes
