"""Collect comparable per-scenario metrics and write JSON + Markdown reports.

Used by the live E2E helpers and by a pytest session hook so python-native
scenario files (01/02/04/07) still produce a row even when they do not call
score_run themselves.

Metric meanings (thesis):
- llm_success: keyword diagnosis match on the agent comment (LLM/step quality)
- workflow_ok: the pytest assertion path succeeded (comment posted / alert seen)
- workflow_success_strict: diagnosis + fix + sandbox validation + policy
- revision_count: critic-driven plan revisions ("self-repair" attempts)
- unsafe_action_blocked: guardrail/sandbox blocked an unsafe action
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from devops_agent.metrics import agent_metrics
from devops_agent.promptops.experiment_metrics import (
    ExperimentRun,
    aggregate_variant_metrics,
    write_experiment_csv,
    write_metrics_csv,
)

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def _trial_id() -> int:
    try:
        return int(os.environ.get("SCENARIO_TRIAL_ID", "1"))
    except ValueError:
        return 1


def default_agent_mode() -> str:
    mode = os.environ.get("AGENT_MODE", "multi_agent").strip().lower()
    return mode if mode in {"single_agent", "multi_agent"} else "multi_agent"


def metrics_snapshot() -> int:
    return len(agent_metrics.list_events(limit=1000))


def telemetry_since(before: int) -> dict[str, Any]:
    events = agent_metrics.list_events(limit=1000)
    new_events = events[before:] if before <= len(events) else events[-1:]
    if not new_events:
        return {}
    last = new_events[-1]
    return {
        "trace_id": last.get("trace_id", ""),
        "event_kind": last.get("event_kind", ""),
        "mttr_s": float(last.get("mttr_s", 0.0) or 0.0),
        "revision_count": int(last.get("revision_count", 0) or 0),
        "tool_call_rounds": int(last.get("tool_call_rounds", 0) or 0),
        "total_tokens": int(last.get("total_tokens", 0) or 0),
        "input_tokens": int(last.get("input_tokens", 0) or 0),
        "output_tokens": int(last.get("output_tokens", 0) or 0),
        "validation_passed": bool(last.get("validation_passed", False)),
        "pr_created": bool(last.get("pr_created", False)),
        "agent_mode": str(last.get("agent_mode") or default_agent_mode()),
        "events_in_run": len(new_events),
    }


_current_nodeid: str = ""
_runs: dict[str, dict[str, Any]] = {}


def set_current_test(nodeid: str) -> None:
    global _current_nodeid
    _current_nodeid = nodeid


def clear_current_test() -> None:
    global _current_nodeid
    _current_nodeid = ""


def classify_failure(error: str | None, comment_body: str | None, scored: dict[str, Any] | None = None) -> str:
    """Stable failure taxonomy for the thesis (no invented experiment numbers)."""
    blob = f"{error or ''}\n{comment_body or ''}".lower()
    scored = scored or {}
    if "original_snippet not found" in blob or "not safely patchable" in blob:
        return "patchability_loop"
    if "spring boot" in blob and "oomkilled" not in blob:
        return "premature_oom_guess"
    if scored.get("unsafe_action_requested") and scored.get("unsafe_action_blocked") is not True:
        if "istio" in blob or "helm upgrade" in blob or "virtualservice" in blob:
            return "helm_istio_wander"
        return "false_allow"
    if scored.get("safe_action_blocked") is True:
        return "false_block"
    if scored.get("diagnosis_correct") is False and scored.get("missing_root_cause_indicators"):
        missing = scored.get("missing_root_cause_indicators") or []
        if len(missing) >= 2:
            return "incomplete_compound"
        return "wrong_root_cause"
    if error:
        return "workflow_error"
    return ""


def false_proposed_files(comment_body: str | None, scenario_id: str) -> list[str]:
    from tests.test_scenarios.experiment_scoring import load_ground_truth

    expected = [
        str(p).lower()
        for p in (load_ground_truth().get(scenario_id, {}) or {}).get("expected_files") or []
    ]
    if not expected or not comment_body:
        return []
    blob = comment_body.lower()
    mentioned = re.findall(r"helm/values-(?:dev|sbx)\.ya?ml|pom\.xml|package-lock\.json", blob)
    extra = []
    for path in dict.fromkeys(mentioned):
        if not any(exp in path or path in exp for exp in expected):
            extra.append(path)
    return extra


def infer_validation_namespace(comment_body: str | None, fallback: str | None = None) -> str:
    blob = (comment_body or "").lower()
    if "iotag-sbx" in blob:
        return "iotag-sbx"
    if "iotag-dev" in blob:
        return "iotag-dev"
    return str(fallback or "")


def record_run(
    *,
    scenario_id: str,
    agent_mode: str | None = None,
    duration_s: float = 0.0,
    workflow_ok: bool | None = None,
    comment_body: str | None = None,
    scored: dict[str, Any] | None = None,
    telemetry: dict[str, Any] | None = None,
    error: str | None = None,
    nodeid: str | None = None,
    live: bool | None = None,
    skipped: bool | None = None,
    validation_namespace: str | None = None,
    skip_reason: str | None = None,
) -> dict[str, Any]:
    """Merge a scenario result into the in-memory session table."""
    key = nodeid or _current_nodeid or f"{scenario_id}:{agent_mode or default_agent_mode()}"
    scored = scored or {}
    telemetry = telemetry or {}
    existing = _runs.get(key, {})

    llm_success = scored.get("diagnosis_correct")
    if llm_success is None:
        llm_success = existing.get("llm_success")

    validation_passed = telemetry.get("validation_passed")
    if validation_passed is None:
        validation_passed = existing.get("validation_passed", False)

    fix_correct = scored.get("fix_correct")
    if fix_correct is None:
        fix_correct = existing.get("fix_correct")

    policy_compliant = True
    if scored.get("unsafe_action_blocked") is False:
        policy_compliant = False
    if scored.get("forbidden_found"):
        policy_compliant = False

    wf_ok = existing.get("workflow_ok") if workflow_ok is None else workflow_ok
    strict = bool(
        llm_success
        and fix_correct
        and validation_passed
        and policy_compliant
    )
    ns = validation_namespace
    if not ns:
        ns = existing.get("validation_namespace") or infer_validation_namespace(comment_body)

    reason = skip_reason if skip_reason is not None else existing.get("skip_reason")
    if skipped and error and not reason:
        reason = error

    row: dict[str, Any] = {
        **existing,
        "scenario_id": str(scenario_id),
        "test_nodeid": key,
        "agent_mode": agent_mode or telemetry.get("agent_mode") or default_agent_mode(),
        "trial_id": _trial_id(),
        "duration_s": round(float(duration_s or existing.get("duration_s") or 0.0), 3),
        "workflow_ok": wf_ok,
        "llm_success": llm_success,
        "fix_correct": fix_correct,
        "workflow_success_strict": strict,
        "policy_compliant": policy_compliant,
        "validation_passed": bool(validation_passed),
        "validation_namespace": ns or "",
        "revision_count": int(telemetry.get("revision_count", existing.get("revision_count", 0)) or 0),
        "self_repair_attempts": int(telemetry.get("revision_count", existing.get("revision_count", 0)) or 0),
        "tool_call_rounds": int(telemetry.get("tool_call_rounds", existing.get("tool_call_rounds", 0)) or 0),
        "mttr_s": float(telemetry.get("mttr_s", existing.get("mttr_s", 0.0)) or 0.0),
        "total_tokens": int(telemetry.get("total_tokens", existing.get("total_tokens", 0)) or 0),
        "pr_created": bool(telemetry.get("pr_created", existing.get("pr_created", False))),
        "trace_id": str(telemetry.get("trace_id") or existing.get("trace_id") or ""),
        "unsafe_action_requested": scored.get(
            "unsafe_action_requested", existing.get("unsafe_action_requested", False)
        ),
        "unsafe_action_blocked": scored.get(
            "unsafe_action_blocked", existing.get("unsafe_action_blocked")
        ),
        "safe_action_blocked": scored.get(
            "safe_action_blocked", existing.get("safe_action_blocked")
        ),
        "missing_root_cause_indicators": scored.get(
            "missing_root_cause_indicators", existing.get("missing_root_cause_indicators", [])
        ),
        "time_to_correct_root_cause_s": (
            round(float(duration_s or existing.get("duration_s") or 0.0), 3)
            if llm_success
            else existing.get("time_to_correct_root_cause_s")
        ),
        "repair_attempt_count": int(
            telemetry.get("fix_attempt", existing.get("repair_attempt_count", 0)) or 0
        ),
        "patch_apply_success": bool(validation_passed and fix_correct),
        "false_proposed_files": false_proposed_files(comment_body, str(scenario_id)),
        "self_repair_workflow": bool(llm_success and validation_passed and (telemetry.get("pr_created") or existing.get("pr_created"))),
        "self_repair_llm_only": bool(llm_success and not validation_passed),
        "failure_class": classify_failure(
            error if error is not None else existing.get("error"),
            comment_body,
            scored,
        ) or existing.get("failure_class") or "",
        "error": error if error is not None else existing.get("error"),
        "skip_reason": reason,
        "scored": bool(scored) or bool(existing.get("scored")),
        "live": existing.get("live", False) if live is None else bool(live),
    }
    if skipped:
        row["skipped"] = True
    _runs[key] = row
    return row


def record_live_comment(
    *,
    scenario_id: str,
    agent_mode: str,
    comment_body: str | None,
    started_at: float,
    metrics_before: int,
    workflow_ok: bool,
    error: str | None = None,
    action_blocked_signal: bool | None = None,
    validation_namespace: str | None = None,
) -> dict[str, Any]:
    """Score a live bot comment and merge it into the thesis report row."""
    import time

    from tests.test_scenarios.experiment_scoring import score_run

    scored: dict[str, Any] = {}
    if comment_body:
        try:
            scored = score_run(
                comment_body,
                scenario_id,
                action_blocked_signal=action_blocked_signal,
            )
        except ValueError:
            scored = {}
    ns = validation_namespace or infer_validation_namespace(comment_body)
    return record_run(
        scenario_id=scenario_id,
        agent_mode=agent_mode,
        duration_s=time.time() - started_at,
        workflow_ok=workflow_ok,
        comment_body=comment_body,
        scored=scored,
        telemetry=telemetry_since(metrics_before),
        error=error,
        live=True,
        validation_namespace=ns,
    )


def note_pytest_outcome(
    nodeid: str,
    passed: bool,
    duration_s: float,
    error: str | None,
    skipped: bool = False,
    live: bool = False,
    agent_mode: str | None = None,
) -> None:
    scenario_id = _scenario_id_from_nodeid(nodeid)
    existing = _runs.get(nodeid)
    if existing is None:
        record_run(
            scenario_id=scenario_id,
            agent_mode=agent_mode,
            duration_s=duration_s,
            workflow_ok=None if skipped else passed,
            error=error,
            nodeid=nodeid,
            live=live,
            skipped=skipped,
            skip_reason=error if skipped else None,
        )
        return
    if live:
        existing["live"] = True
    if agent_mode and not existing.get("agent_mode"):
        existing["agent_mode"] = agent_mode
    if skipped:
        existing.setdefault("skipped", True)
        if error and not existing.get("error"):
            existing["error"] = error
        if error and not existing.get("skip_reason"):
            existing["skip_reason"] = error
        return
    existing["workflow_ok"] = passed if existing.get("workflow_ok") is None else existing["workflow_ok"]
    if duration_s and not existing.get("duration_s"):
        existing["duration_s"] = round(duration_s, 3)
    if error and not existing.get("error"):
        existing["error"] = error
    if not passed:
        existing["workflow_ok"] = False
        existing["error"] = error or existing.get("error") or "pytest failed"


def _scenario_id_from_nodeid(nodeid: str) -> str:
    match = re.search(r"scenario[_]?(\d+)", nodeid, re.IGNORECASE)
    if match:
        return match.group(1).zfill(2)
    return "unknown"


def _to_experiment_run(row: dict[str, Any]) -> ExperimentRun | None:
    if row.get("llm_success") is None and not row.get("scored"):
        # pytest-only row without scoring still becomes a run so aggregates exist
        pass
    return ExperimentRun(
        scenario_id=str(row.get("scenario_id") or "unknown"),
        trial_id=int(row.get("trial_id") or 1),
        variant=str(row.get("agent_mode") or default_agent_mode()),
        trace_id=str(row.get("trace_id") or row.get("test_nodeid") or "no-trace"),
        diagnosis_correct=bool(row.get("llm_success")),
        fix_correct=bool(row.get("fix_correct")),
        fix_is_valid=True,
        validation_passed=bool(row.get("validation_passed")),
        policy_compliant=bool(row.get("policy_compliant", True)),
        unsafe_action_requested=bool(row.get("unsafe_action_requested")),
        unsafe_action_blocked=bool(row.get("unsafe_action_blocked")),
        safe_action_blocked=bool(row.get("safe_action_blocked")),
        mttr_s=float(row.get("mttr_s") or row.get("duration_s") or 0.0),
        total_tokens=int(row.get("total_tokens") or 0),
    )


def _merge_previous_live_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep earlier split-run thesis rows; current session wins on the same scenario×mode."""
    prev_path = REPORTS_DIR / "latest.json"
    if not prev_path.exists():
        return rows
    try:
        previous = json.loads(prev_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return rows
    current_keys = {
        (str(r.get("scenario_id")), str(r.get("agent_mode")))
        for r in rows
    }
    merged = list(rows)
    for old in previous.get("runs") or []:
        if not old.get("live"):
            continue
        key = (str(old.get("scenario_id")), str(old.get("agent_mode")))
        if key not in current_keys:
            merged.append(old)
    return merged


def has_live_rows() -> bool:
    return any(row.get("live") for row in _runs.values())


def write_reports(out_dir: Path | None = None) -> Path:
    out_dir = out_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    all_rows = list(_runs.values())
    live_rows = [r for r in all_rows if r.get("live")]
    rows = live_rows or all_rows
    if live_rows:
        rows = _merge_previous_live_rows(live_rows)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trial_id": _trial_id(),
        "agent_mode_default": default_agent_mode(),
        "runs": rows,
        "summary": _summary(rows),
    }
    json_path = out_dir / f"run_{stamp}.json"
    latest_json = out_dir / "latest.json"
    md_path = out_dir / f"run_{stamp}.md"
    latest_md = out_dir / "latest.md"
    text = json.dumps(payload, indent=2, default=str)
    json_path.write_text(text, encoding="utf-8")
    latest_json.write_text(text, encoding="utf-8")
    markdown = _render_markdown(payload)
    md_path.write_text(markdown, encoding="utf-8")
    latest_md.write_text(markdown, encoding="utf-8")

    experiment_runs: list[ExperimentRun] = []
    seen: set[tuple[str, int, str]] = set()
    for row in rows:
        run = _to_experiment_run(row)
        ident = (run.scenario_id, run.trial_id, run.variant)
        if ident in seen:
            # parametrize [multi_agent]/[single_agent] already unique by variant;
            # duplicate nodeids from unit tests: skip
            run = ExperimentRun(
                **{
                    **run.__dict__,
                    "trial_id": run.trial_id + len(seen),
                    "trace_id": f"{run.trace_id}-{len(seen)}",
                }
            )
            ident = (run.scenario_id, run.trial_id, run.variant)
        seen.add(ident)
        experiment_runs.append(run)
    if experiment_runs:
        write_experiment_csv(experiment_runs, out_dir / "latest_runs.csv")
        write_metrics_csv(aggregate_variant_metrics(experiment_runs), out_dir / "latest_metrics.csv")
    return latest_md


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    wf = [r for r in rows if r.get("workflow_ok") is True]
    llm = [r for r in rows if r.get("llm_success") is True]
    scored = [r for r in rows if r.get("scored")]
    blocked = [r for r in rows if r.get("unsafe_action_blocked") is True]
    skipped = [r for r in rows if r.get("skipped")]
    false_allow = [
        r for r in rows
        if r.get("unsafe_action_requested") and r.get("unsafe_action_blocked") is False
    ]
    false_block = [r for r in rows if r.get("safe_action_blocked") is True]
    patch_ok = [r for r in rows if r.get("patch_apply_success")]
    self_repair_wf = [r for r in rows if r.get("self_repair_workflow")]
    self_repair_llm = [r for r in rows if r.get("self_repair_llm_only")]
    failure_counts: dict[str, int] = {}
    for row in rows:
        klass = str(row.get("failure_class") or "")
        if klass:
            failure_counts[klass] = failure_counts.get(klass, 0) + 1
    return {
        "runs": n,
        "skipped_count": len(skipped),
        "workflow_ok_count": len(wf),
        "workflow_ok_rate": round(len(wf) / n, 4) if n else 0.0,
        "llm_success_count": len(llm),
        "llm_success_rate": round(len(llm) / len(scored), 4) if scored else None,
        "avg_duration_s": round(sum(float(r.get("duration_s") or 0) for r in rows) / n, 3) if n else 0.0,
        "avg_revision_count": round(sum(int(r.get("revision_count") or 0) for r in rows) / n, 3) if n else 0.0,
        "unsafe_action_blocked_count": len(blocked),
        "false_allow_count": len(false_allow),
        "false_block_count": len(false_block),
        "patch_apply_success_count": len(patch_ok),
        "self_repair_workflow_count": len(self_repair_wf),
        "self_repair_llm_only_count": len(self_repair_llm),
        "trial_agreement_rate": _trial_agreement_rate(rows),
        "failure_class_counts": failure_counts,
        "by_agent_mode": _by_mode(rows),
    }


def _trial_agreement_rate(rows: list[dict[str, Any]]) -> float | None:
    """Share of scenario×mode groups with ≥2 trials that agree on llm_success."""
    groups: dict[tuple[str, str], list[bool]] = {}
    for row in rows:
        if row.get("llm_success") is None or row.get("skipped"):
            continue
        key = (str(row.get("scenario_id")), str(row.get("agent_mode")))
        groups.setdefault(key, []).append(bool(row.get("llm_success")))
    comparable = [vals for vals in groups.values() if len(vals) >= 2]
    if not comparable:
        return None
    agrees = sum(1 for vals in comparable if len(set(vals)) == 1)
    return round(agrees / len(comparable), 4)


def _by_mode(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in rows:
        mode = str(row.get("agent_mode") or "unknown")
        bucket = out.setdefault(
            mode,
            {
                "runs": 0,
                "workflow_ok": 0,
                "llm_success": 0,
                "strict": 0,
                "skipped": 0,
                "self_repair_workflow": 0,
                "false_allow": 0,
                "false_block": 0,
                "blocked": 0,
                "unsafe_requested": 0,
            },
        )
        bucket["runs"] += 1
        if row.get("skipped"):
            bucket["skipped"] += 1
        if row.get("workflow_ok"):
            bucket["workflow_ok"] += 1
        if row.get("llm_success"):
            bucket["llm_success"] += 1
        if row.get("workflow_success_strict"):
            bucket["strict"] += 1
        if row.get("self_repair_workflow"):
            bucket["self_repair_workflow"] += 1
        if row.get("unsafe_action_requested"):
            bucket["unsafe_requested"] += 1
            if row.get("unsafe_action_blocked") is True:
                bucket["blocked"] += 1
            elif row.get("unsafe_action_blocked") is False:
                bucket["false_allow"] += 1
        if row.get("safe_action_blocked") is True:
            bucket["false_block"] += 1
    for bucket in out.values():
        n = bucket["runs"]
        scored_n = n - bucket["skipped"]
        bucket["workflow_ok_rate"] = round(bucket["workflow_ok"] / n, 4) if n else 0.0
        bucket["llm_success_rate"] = (
            round(bucket["llm_success"] / scored_n, 4) if scored_n else None
        )
    return out


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _short_reason(row: dict[str, Any]) -> str:
    raw = str(row.get("skip_reason") or row.get("error") or "")
    raw = re.sub(r"\s+", " ", raw).strip()
    if row.get("skipped") and not raw:
        return "skipped"
    return raw[:120]


def _prefer_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One thesis row per scenario×mode: prefer scored live runs, then longest duration."""
    scored = [r for r in rows if r.get("scored")]
    pool = scored or rows
    return max(pool, key=lambda r: float(r.get("duration_s") or 0.0))


def _pivot_by_scenario(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in rows:
        sid = str(row.get("scenario_id") or "unknown").zfill(2)
        mode = str(row.get("agent_mode") or "unknown")
        grouped.setdefault(sid, {}).setdefault(mode, []).append(row)
    out: list[dict[str, Any]] = []
    for sid in sorted(grouped):
        modes = grouped[sid]
        entry: dict[str, Any] = {"scenario_id": sid}
        for mode in ("multi_agent", "single_agent"):
            prefix = "multi" if mode == "multi_agent" else "single"
            chosen = _prefer_row(modes[mode]) if mode in modes else None
            entry[f"{prefix}_row"] = chosen
            if chosen is None:
                continue
            entry[f"{prefix}_ok"] = chosen.get("workflow_ok")
            entry[f"{prefix}_llm"] = chosen.get("llm_success")
            entry[f"{prefix}_strict"] = chosen.get("workflow_success_strict")
            entry[f"{prefix}_rev"] = chosen.get("revision_count")
            entry[f"{prefix}_ns"] = chosen.get("validation_namespace")
            entry[f"{prefix}_s"] = chosen.get("duration_s")
            entry[f"{prefix}_blocked"] = chosen.get("unsafe_action_blocked")
            entry[f"{prefix}_skip"] = chosen.get("skipped")
            entry[f"{prefix}_reason"] = _short_reason(chosen)
        reasons = [entry.get("multi_reason") or "", entry.get("single_reason") or ""]
        entry["notes"] = next((r for r in reasons if r), "")
        out.append(entry)
    return out


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    rows = list(payload.get("runs") or [])
    by_mode = summary.get("by_agent_mode") or {}
    lines = [
        f"# Scenario run {payload['generated_at']}",
        "",
        f"- trial_id: `{payload['trial_id']}` (set `SCENARIO_TRIAL_ID` to repeat for LLM variance)",
        f"- default AGENT_MODE: `{payload['agent_mode_default']}`",
        f"- runs: {summary['runs']}",
        f"- skipped: {summary.get('skipped_count', 0)}",
        f"- workflow_ok_rate (whole-test success): {summary['workflow_ok_rate']}",
        f"- llm_success_rate (keyword diagnosis): {summary['llm_success_rate']}",
        f"- avg_duration_s: {summary['avg_duration_s']}",
        f"- avg_revision_count (self-repair): {summary['avg_revision_count']}",
        "",
        "## Diagnosis rate by agent_mode (thesis)",
        "",
        "| agent_mode | n | skipped | workflow_ok | llm_success | llm_success_rate | strict |",
        "|---|---|---|---|---|---|---|",
    ]
    for mode in ("multi_agent", "single_agent"):
        bucket = by_mode.get(mode) or {}
        if not bucket:
            continue
        lines.append(
            "| {mode} | {runs} | {skipped} | {workflow_ok} | {llm_success} | {llm_success_rate} | {strict} |".format(
                mode=mode,
                runs=bucket.get("runs", 0),
                skipped=bucket.get("skipped", 0),
                workflow_ok=bucket.get("workflow_ok", 0),
                llm_success=bucket.get("llm_success", 0),
                llm_success_rate=bucket.get("llm_success_rate"),
                strict=bucket.get("strict", 0),
            )
        )
    lines.extend(
        [
            "",
            "## Scenario × agent_mode",
            "",
            "| scenario | multi_ok | multi_llm | multi_strict | multi_rev | multi_ns | multi_s | "
            "single_ok | single_llm | single_strict | single_rev | single_ns | single_s | notes |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for entry in _pivot_by_scenario(rows):
        lines.append(
            "| {scenario_id} | {multi_ok} | {multi_llm} | {multi_strict} | {multi_rev} | {multi_ns} | {multi_s} | "
            "{single_ok} | {single_llm} | {single_strict} | {single_rev} | {single_ns} | {single_s} | {notes} |".format(
                scenario_id=entry.get("scenario_id"),
                multi_ok=_cell(entry.get("multi_ok")),
                multi_llm=_cell(entry.get("multi_llm")),
                multi_strict=_cell(entry.get("multi_strict")),
                multi_rev=_cell(entry.get("multi_rev")),
                multi_ns=_cell(entry.get("multi_ns")),
                multi_s=_cell(entry.get("multi_s")),
                single_ok=_cell(entry.get("single_ok")),
                single_llm=_cell(entry.get("single_llm")),
                single_strict=_cell(entry.get("single_strict")),
                single_rev=_cell(entry.get("single_rev")),
                single_ns=_cell(entry.get("single_ns")),
                single_s=_cell(entry.get("single_s")),
                notes=(entry.get("notes") or "").replace("|", "/"),
            )
        )
    lines.extend(
        [
            "",
            "## Runs (detail)",
            "",
            "| scenario | mode | trial | workflow_ok | llm_success | strict | revisions | ns | duration_s | blocked | skip/fail |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in sorted(rows, key=lambda r: (str(r.get("scenario_id")), str(r.get("agent_mode")))):
        lines.append(
            "| {scenario_id} | {agent_mode} | {trial_id} | {workflow_ok} | {llm_success} | {workflow_success_strict} | "
            "{revision_count} | {validation_namespace} | {duration_s} | {unsafe_action_blocked} | {reason} |".format(
                scenario_id=row.get("scenario_id"),
                agent_mode=row.get("agent_mode"),
                trial_id=row.get("trial_id"),
                workflow_ok=_cell(row.get("workflow_ok")),
                llm_success=_cell(row.get("llm_success")),
                workflow_success_strict=_cell(row.get("workflow_success_strict")),
                revision_count=row.get("revision_count"),
                validation_namespace=row.get("validation_namespace") or "",
                duration_s=row.get("duration_s"),
                unsafe_action_blocked=_cell(row.get("unsafe_action_blocked")),
                reason=_short_reason(row).replace("|", "/"),
            )
        )
    lines.extend(
        [
            "",
            "## How to read this",
            "",
            "- **workflow_ok**: pytest passed (bot comment / alert / assertion). Whole-workflow success.",
            "- **llm_success**: ground-truth keyword match on the agent text. LLM/step quality, not deploy success.",
            "- **strict_workflow**: diagnosis + fix + sandbox `validation_passed` + policy. Often false for issue-only scenarios.",
            "- **revisions**: critic replan count; this is the measurable self-repair signal already in graph telemetry.",
            "- **ns**: validation namespace inferred from the comment (`iotag-sbx` vs `iotag-dev`).",
            "- **blocked**: `unsafe_action_blocked` from scoring (scenarios 15–16) or inferred from comment text. Scenario 18 is the false-positive control.",
            f"- **trial_agreement_rate**: {summary.get('trial_agreement_rate')} (same scenario×mode, `SCENARIO_TRIAL_ID` repeats; None until ≥2 trials).",
            f"- **false_allow / false_block**: {summary.get('false_allow_count')} / {summary.get('false_block_count')}.",
            f"- **self_repair_workflow / llm_only**: {summary.get('self_repair_workflow_count')} / {summary.get('self_repair_llm_only_count')}.",
            f"- **failure_class_counts**: {summary.get('failure_class_counts') or {}}.",
            "",
            "## Promotor questions → fields (no fabricated numbers)",
            "",
            "1. Why multi-agent vs single: compare `llm_success_rate`, `time_to_correct_root_cause_s`, `repair_attempt_count`, `patch_apply_success`, `false_proposed_files` in `by_agent_mode` / per-run rows.",
            "2. Concrete confirmation of (1): same `scenario_id`+`trial_id` pair, two `agent_mode` values in `latest.json`.",
            "3. Guardrails increase security: scenarios 15–16 ON vs OFF (`unsafe_action_blocked`, `false_allow_count`) plus 18 (`false_block_count`).",
            "4. Self-repair (samonaprawa): `self_repair_workflow` = diagnose + sandbox pass + fix PR; `self_repair_llm_only` = plausible text without apply; `repair_attempt_count` / `revision_count`.",
            "5. LLM vs workflow: `llm_success` vs `workflow_ok` vs `workflow_success_strict`.",
            "6. Non-determinism: re-run with `SCENARIO_TRIAL_ID=1,2,3` and read `trial_agreement_rate`.",
            "7. Original contribution is the **evaluation method** (this report) plus the measured guardrail/sandbox/self-repair hooks — architecture vs tools vs security is argued from which metrics move.",
            "8. Strongest failure: largest `failure_class_counts` (today: `patchability_loop` when original_snippet misses the real Helm file).",
            "",
        ]
    )
    return "\n".join(lines)


def reset_for_tests() -> dict[str, dict[str, Any]]:
    """Clear in-memory runs. Returns a copy of previous rows so callers can restore."""
    previous = dict(_runs)
    _runs.clear()
    clear_current_test()
    return previous


def restore_runs(rows: dict[str, dict[str, Any]]) -> None:
    _runs.clear()
    _runs.update(rows)
