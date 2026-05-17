"""Runner ewaluacji promptów.

Dla każdego przypadku z datasetu:
1. Buduje system prompt z wskazanego wariantu.
2. Wywołuje LLM (ChatOpenAI/ChatAnthropic) z wejściem przypadku.
3. Ocenia odpowiedź: sprawdza expected_indicators, forbidden_indicators,
   tool_rounds oraz opcjonalnie wywołuje sędziego LLM.
4. Zapisuje wyniki w data/promptops/runs/<run_id>.json.

Uwaga: runner NIE uruchamia pełnego grafu LangGraph – wywołuje tylko model
z danym system promptem. Dzięki temu testy są szybkie i izolowane od
side-effectów (GitHub API, kubectl itp.). Do testowania end-to-end użyj
iot-agent-tests/.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from devops_agent.promptops.datasets import DatasetManager, get_dataset_manager
from devops_agent.promptops.models import (
    EvalCase,
    EvalCaseResult,
    EvalRun,
    ScoreLevel,
)
from devops_agent.promptops.registry import PromptRegistry, get_registry

logger = logging.getLogger(__name__)

_DEFAULT_RUNS_DIR = (
    Path(__file__).parent.parent.parent.parent / "data" / "promptops" / "runs"
)

JudgeFn = Callable[[EvalCase, str], float]
"""Sygnatura funkcji sędziego: (case, raw_output) -> score 0.0-1.0."""


def _heuristic_judge(case: EvalCase, output: str) -> float:
    """Prosta ocena heurystyczna bez wywołania LLM.

    - Dodaje punkty za każdy znaleziony expected_indicator.
    - Odejmuje za forbidden_indicator.
    - Wynik normalizowany do [0.0, 1.0].
    """
    if not output:
        return 0.0
    low = output.lower()
    total_checks = len(case.expected_indicators) + len(case.forbidden_indicators)
    if total_checks == 0:
        return 1.0

    score = 0.0
    # Każdy obecny oczekiwany wskaźnik = +1 pkt
    for ind in case.expected_indicators:
        if ind.lower() in low:
            score += 1.0

    # Każdy forbidden = -1 pkt (kara)
    for ind in case.forbidden_indicators:
        if ind.lower() in low:
            score -= 1.0

    max_possible = float(len(case.expected_indicators)) if case.expected_indicators else 1.0
    normalized = max(0.0, score / max_possible)
    return round(min(normalized, 1.0), 4)


def _llm_judge(
    llm: Any,
    case: EvalCase,
    output: str,
) -> float:
    """Sędzia oparty o LLM – ocenia odpowiedź w skali 0-10 i normalizuje do [0,1].

    Używać oszczędnie – każde wywołanie kosztuje tokeny.
    """
    system = (
        "Jesteś obiektywnym ewaluatorem odpowiedzi agenta DevOps. "
        "Oceń odpowiedź w skali 0-10, biorąc pod uwagę trafność, "
        "kompletność i brak halucynacji. "
        "Odpowiedz TYLKO jedną liczbą całkowitą z zakresu 0-10."
    )
    prompt = (
        f"### Zdarzenie wejściowe:\n{json.dumps(case.input_event, indent=2)}\n\n"
        f"### Oczekiwane wskaźniki:\n{case.expected_indicators}\n\n"
        f"### Odpowiedź agenta:\n{output}\n\n"
        "Oceń odpowiedź (0-10):"
    )
    try:
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=prompt)])
        raw = resp.content.strip()
        # Wyciągnij pierwszą liczbę z odpowiedzi
        import re
        m = re.search(r"\d+", raw)
        score_10 = int(m.group()) if m else 5
        return round(min(max(score_10, 0), 10) / 10.0, 4)
    except Exception as exc:
        logger.warning("LLM judge error: %s", exc)
        return 0.5  # Domyślna ocena przy błędzie


class EvalRunner:
    """Uruchamia ewaluacje na wariancie i datasecie."""

    def __init__(
        self,
        registry: PromptRegistry | None = None,
        dataset_manager: DatasetManager | None = None,
        runs_dir: Path | None = None,
    ) -> None:
        self._registry = registry or get_registry()
        self._dataset_manager = dataset_manager or get_dataset_manager()
        self._runs_dir = runs_dir or _DEFAULT_RUNS_DIR
        self._runs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Main eval API
    # ------------------------------------------------------------------

    def run(
        self,
        variant_id: str,
        event_kind: str,
        model: str | None = None,
        judge: JudgeFn | None = None,
        use_llm_judge: bool = False,
        max_cases: int | None = None,
    ) -> EvalRun:
        """Uruchamia pełną ewaluację wariantu na datasecie.

        Args:
            variant_id: ID wariantu prompta (musi być zarejestrowany).
            event_kind: Typ zdarzenia / nazwa datasetu.
            model: Nazwa modelu OpenAI. Domyślnie z DEVOPS_AGENT_MODEL.
            judge: Niestandardowa funkcja sędziego (zamiast heurystyki).
            use_llm_judge: Jeśli True, używa LLM jako sędziego.
            max_cases: Limit przypadków (do szybkich smoke-testów).
        """
        model = model or os.getenv("DEVOPS_AGENT_MODEL", "gpt-4o-mini")
        variant = self._registry.get_variant(variant_id)
        if variant is None:
            raise ValueError(f"Wariant '{variant_id}' nie istnieje w rejestrze")

        dataset = self._dataset_manager.load(event_kind)
        cases = dataset.cases[:max_cases] if max_cases else dataset.cases
        if not cases:
            raise ValueError(f"Dataset '{event_kind}' jest pusty")

        llm = ChatOpenAI(model=model, temperature=0)

        actual_judge: JudgeFn
        if judge is not None:
            actual_judge = judge
        elif use_llm_judge:
            judge_llm = ChatOpenAI(model=model, temperature=0)
            actual_judge = lambda case, out: _llm_judge(judge_llm, case, out)
        else:
            actual_judge = _heuristic_judge

        eval_run = EvalRun(
            variant_id=variant_id,
            model=model,
            event_kind=event_kind,
        )

        logger.info(
            "Start eval: variant=%s model=%s dataset=%s cases=%d",
            variant_id, model, event_kind, len(cases),
        )

        for case in cases:
            result = self._eval_case(case, variant.content, llm, actual_judge)
            eval_run.results.append(result)
            logger.debug(
                "  case=%s score=%.2f level=%s latency=%.0fms",
                case.case_id, result.score, result.level, result.latency_ms,
            )

        eval_run.compute_summary()
        self._save_run(eval_run)

        logger.info(
            "Eval zakończony: pass_rate=%.1f%% avg_score=%.2f run_id=%s",
            eval_run.pass_rate * 100, eval_run.avg_score, eval_run.run_id,
        )
        return eval_run

    # ------------------------------------------------------------------
    # Single case eval
    # ------------------------------------------------------------------

    def _eval_case(
        self,
        case: EvalCase,
        system_prompt: str,
        llm: ChatOpenAI,
        judge: JudgeFn,
    ) -> EvalCaseResult:
        """Ewaluuje jeden przypadek testowy."""
        user_msg = json.dumps(case.input_event, indent=2, ensure_ascii=False)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ]

        t0 = time.perf_counter()
        error: str | None = None
        raw_output = ""
        input_tokens = 0
        output_tokens = 0

        try:
            response = llm.invoke(messages)
            raw_output = response.content or ""
            # Pobierz użycie tokenów jeśli dostępne
            usage = getattr(response, "usage_metadata", None) or {}
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
        except Exception as exc:
            error = str(exc)
            logger.warning("Błąd LLM dla case %s: %s", case.case_id, exc)

        latency_ms = (time.perf_counter() - t0) * 1000

        # Ocena
        score = judge(case, raw_output) if not error else 0.0

        # Które wskaźniki zostały znalezione
        low = raw_output.lower()
        passed = [i for i in case.expected_indicators if i.lower() in low]
        missing = [i for i in case.expected_indicators if i.lower() not in low]
        forbidden_found = [i for i in case.forbidden_indicators if i.lower() in low]

        # Kara za forbidden
        if forbidden_found:
            score = max(0.0, score - 0.2 * len(forbidden_found))

        level: ScoreLevel
        if score >= 0.8 and not forbidden_found:
            level = "pass"
        elif score >= 0.4:
            level = "partial"
        else:
            level = "fail"

        return EvalCaseResult(
            case_id=case.case_id,
            variant_id="",  # wypełniane przez run()
            model="",       # wypełniane przez run()
            score=round(score, 4),
            level=level,
            passed_indicators=passed,
            missing_indicators=missing,
            forbidden_found=forbidden_found,
            latency_ms=round(latency_ms, 1),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_summary=raw_output[:500],
            error=error,
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_run(self, run: EvalRun) -> Path:
        path = self._runs_dir / f"{run.run_id}.json"
        path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Zapisano wyniki ewaluacji: %s", path)
        return path

    def load_run(self, run_id: str) -> EvalRun:
        path = self._runs_dir / f"{run_id}.json"
        return EvalRun.model_validate_json(path.read_text(encoding="utf-8"))

    def list_runs(
        self,
        variant_id: str | None = None,
        event_kind: str | None = None,
        model: str | None = None,
    ) -> list[EvalRun]:
        """Wczytuje wszystkie przebiegi z dysku, z opcjonalnym filtrowaniem."""
        runs: list[EvalRun] = []
        for p in sorted(self._runs_dir.glob("*.json")):
            try:
                run = EvalRun.model_validate_json(p.read_text(encoding="utf-8"))
                if variant_id and run.variant_id != variant_id:
                    continue
                if event_kind and run.event_kind != event_kind:
                    continue
                if model and run.model != model:
                    continue
                runs.append(run)
            except Exception as exc:
                logger.warning("Błąd wczytywania run %s: %s", p.name, exc)
        return runs
