"""Modele danych dla systemu PromptOps."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Prompt variants
# ---------------------------------------------------------------------------

class PromptVariant(BaseModel):
    """Jedna wersja prompta identyfikowana unikalnym variant_id."""

    name: str
    """Nazwa prompta, np. 'ci_fixer' – odpowiada sekcji w _build_system_prompt."""

    variant_id: str
    """Unikalny identyfikator wariantu, np. 'ci_fixer_v1'."""

    content: str
    """Treść prompta (markdown)."""

    weight: float = 1.0
    """Waga w losowaniu A/B (wyżej = częściej wybierany)."""

    active: bool = True
    """Czy wariant jest aktywny."""

    description: str = ""
    """Opis zmian względem poprzedniej wersji."""

    created_at: datetime = Field(default_factory=datetime.utcnow)


class ABAssignment(BaseModel):
    """Przypisanie wariantu do konkretnego trace_id."""

    trace_id: str
    variant_id: str
    assigned_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Eval datasets
# ---------------------------------------------------------------------------

class EvalCase(BaseModel):
    """Pojedynczy przypadek testowy do ewaluacji."""

    case_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    event_kind: str
    """Typ zdarzenia, np. 'github_ci_failure'."""

    input_event: dict[str, Any]
    """Pełne wejście do agenta – payload zdarzenia."""

    expected_indicators: list[str] = Field(default_factory=list)
    """Frazy/słowa kluczowe, które MUSZĄ pojawić się w odpowiedzi agenta."""

    forbidden_indicators: list[str] = Field(default_factory=list)
    """Frazy, które NIE MOGĄ pojawić się w odpowiedzi (guard rails)."""

    expected_tools: list[str] = Field(default_factory=list)
    """Narzędzia, które agent POWINIEN wywołać."""

    max_tool_rounds: int = 10
    """Limit rund narzędziowych (efektywność planu)."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Dowolne dodatkowe info: źródło, autor, link do incydentu itp."""


class EvalDataset(BaseModel):
    """Zbiór przypadków testowych dla jednego event_kind."""

    event_kind: str
    description: str = ""
    cases: list[EvalCase] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Eval results
# ---------------------------------------------------------------------------

ScoreLevel = Literal["pass", "partial", "fail"]


class EvalCaseResult(BaseModel):
    """Wynik ewaluacji jednego przypadku."""

    case_id: str
    variant_id: str
    model: str
    score: float
    """0.0 – 1.0."""

    level: ScoreLevel
    passed_indicators: list[str] = Field(default_factory=list)
    missing_indicators: list[str] = Field(default_factory=list)
    forbidden_found: list[str] = Field(default_factory=list)
    tools_called: list[str] = Field(default_factory=list)
    tool_rounds: int = 0
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    raw_summary: str = ""
    error: str | None = None


class EvalRun(BaseModel):
    """Wyniki całego przebiegu ewaluacji."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    variant_id: str
    model: str
    event_kind: str
    results: list[EvalCaseResult] = Field(default_factory=list)

    # Agregaty
    total_cases: int = 0
    passed: int = 0
    partial: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    avg_score: float = 0.0
    avg_latency_ms: float = 0.0
    avg_tokens: float = 0.0

    def compute_summary(self) -> None:
        """Oblicza pola agregatów na podstawie results."""
        self.total_cases = len(self.results)
        self.passed = sum(1 for r in self.results if r.level == "pass")
        self.partial = sum(1 for r in self.results if r.level == "partial")
        self.failed = sum(1 for r in self.results if r.level == "fail")
        self.pass_rate = self.passed / self.total_cases if self.total_cases else 0.0
        self.avg_score = (
            sum(r.score for r in self.results) / self.total_cases
            if self.total_cases else 0.0
        )
        self.avg_latency_ms = (
            sum(r.latency_ms for r in self.results) / self.total_cases
            if self.total_cases else 0.0
        )
        total_toks = [r.input_tokens + r.output_tokens for r in self.results]
        self.avg_tokens = sum(total_toks) / self.total_cases if self.total_cases else 0.0


# ---------------------------------------------------------------------------
# Regression & benchmarks
# ---------------------------------------------------------------------------

class RegressionReport(BaseModel):
    """Raport porównania dwóch przebiegów ewaluacji."""

    baseline_run_id: str
    candidate_run_id: str
    baseline_pass_rate: float
    candidate_pass_rate: float
    delta: float
    """candidate - baseline (ujemna = regresja)."""

    regression_detected: bool
    threshold: float
    """Próg, poniżej którego delta uznana jest za regresję."""

    per_case_delta: dict[str, float] = Field(default_factory=dict)
    """case_id -> delta score dla każdego przypadku."""

    notes: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class BenchmarkEntry(BaseModel):
    """Wynik jednego modelu w benchmarku."""

    model: str
    variant_id: str
    pass_rate: float
    avg_score: float
    avg_latency_ms: float
    avg_tokens: float
    run_id: str


class BenchmarkReport(BaseModel):
    """Raport porównania wielu modeli na tym samym datasecie."""

    event_kind: str
    variant_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    entries: list[BenchmarkEntry] = Field(default_factory=list)

    @property
    def best_model(self) -> str | None:
        if not self.entries:
            return None
        return max(self.entries, key=lambda e: e.pass_rate).model
