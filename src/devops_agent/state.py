from typing import Any, Literal, TypedDict


RiskLevel = Literal["low", "medium", "high"]

# Security threat types surfaced by the SecurityLayer
SecurityThreatType = Literal[
    "prompt_injection",
    "jailbreak",
    "secret_leakage",
    "pii_leakage",
    "data_exfiltration",
    "malicious_tool_call",
]


class PlanStep(TypedDict):
    tool: str
    action: str
    args: dict[str, Any]


class AgentState(TypedDict, total=False):
    trace_id: str
    request: str
    dry_run: bool
    context: dict[str, Any]
    started_at: float | None
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
    ci_fix_proposal: dict[str, Any] | None      # {files: [{path, content}], root_cause: str}
    validation_result: dict[str, Any] | None    # {passed: bool, errors: list[str], output: str}
    pr_url: str | None                          # URL of the created PR (if opened)
    fix_target_branch: str | None               # branch the fix PR should target (PR branch for pr_build_failure, main otherwise)
    fix_pr_number: int | None                   # PR number that triggered a pr_build_failure event
    fix_attempt: int                            # current attempt number (incremented by ci_fixer before each attempt)
    max_fix_attempts: int                       # max retries allowed (default 3)
    validation_history: list[dict[str, Any]]    # all past validation_result dicts (fed back to ci_fixer)
    token_usage: dict[str, int]                 # {input_tokens, output_tokens, tool_call_rounds, plan_step_count}
    messages: list[Any]                         # LangChain message history for tool calls
    security_violations: list[dict[str, str]]   # serialised SecurityViolation dicts accumulated across the run
    security_blocked: bool                      # True when a critical/high threat caused the run to be halted
    guardrails_enabled: bool                    # False when GUARDRAILS_ENABLED=0 (ablation)
    requested_forbidden_actions: list[str]      # forbidden actions detected in input (may be unblocked)
    forbidden_tools_requested: list[str]        # lethal tools the executor intercepted and did not run
