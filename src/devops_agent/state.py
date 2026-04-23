from typing import Any, Literal, TypedDict


RiskLevel = Literal["low", "medium", "high"]


class PlanStep(TypedDict):
    tool: str
    action: str
    args: dict[str, Any]


class AgentState(TypedDict, total=False):
    request: str
    dry_run: bool
    context: dict[str, Any]
    plan: list[PlanStep]
    critique_passed: bool
    critique_notes: list[str]
    revision_count: int
    max_revisions: int
    risk_level: RiskLevel
    execution_results: list[dict[str, Any]]
    execution_summary: str
    final_summary: str
    event_kind: str
    issue_title: str
    issue_body: str
