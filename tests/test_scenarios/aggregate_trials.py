"""Merge per-trial scenario reports into one cross-trial thesis report.

``run_metrics.write_reports`` keys previous rows by (scenario_id, agent_mode) only, so a
later trial overwrites an earlier one and ``trial_agreement_rate`` can never be computed.
This module keeps every ``reports/trial<N>.json`` snapshot and rebuilds
``reports/latest.json`` / ``latest.md`` from the union, keyed by
(scenario_id, agent_mode, trial_id).

Usage:
    python -m tests.test_scenarios.aggregate_trials
    python -m tests.test_scenarios.aggregate_trials --snapshot 2   # latest.json -> trial2.json
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.test_scenarios.run_metrics import (
    REPORTS_DIR,
    _render_markdown,
    _summary,
)


def snapshot_trial(trial_id: int, reports_dir: Path | None = None) -> Path:
    """Copy the just-written latest.json to a stable per-trial file."""
    reports_dir = reports_dir or REPORTS_DIR
    src = reports_dir / "latest.json"
    dest = reports_dir / f"trial{trial_id}.json"
    if not src.exists():
        raise FileNotFoundError(f"No latest.json to snapshot at {src}")
    shutil.copy2(src, dest)
    return dest


def _row_key(row: dict[str, Any]) -> tuple[str, str, str, int]:
    """Identity of a single measurement.

    ``test_nodeid`` is part of the key so the guardrails_on/guardrails_off ablation arms of
    scenarios 15/16 stay separate rows; collapsing them would erase ``unsafe_action_blocked``.
    """
    return (
        str(row.get("scenario_id") or "unknown").zfill(2),
        str(row.get("agent_mode") or "unknown"),
        str(row.get("test_nodeid") or ""),
        int(row.get("trial_id") or 1),
    )


def _better(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Prefer a scored live row, then the longer run (more work actually happened)."""
    if bool(candidate.get("scored")) != bool(current.get("scored")):
        return candidate if candidate.get("scored") else current
    if bool(candidate.get("live")) != bool(current.get("live")):
        return candidate if candidate.get("live") else current
    cur_d = float(current.get("duration_s") or 0.0)
    cand_d = float(candidate.get("duration_s") or 0.0)
    return candidate if cand_d > cur_d else current


def collect_rows(reports_dir: Path | None = None) -> tuple[list[dict[str, Any]], list[int]]:
    """Union of all trial snapshots, deduped by scenario x mode x trial."""
    reports_dir = reports_dir or REPORTS_DIR
    merged: dict[tuple[str, str, int], dict[str, Any]] = {}
    trials: set[int] = set()
    for path in sorted(reports_dir.glob("trial*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in payload.get("runs") or []:
            key = _row_key(row)
            trials.add(key[-1])
            existing = merged.get(key)
            merged[key] = row if existing is None else _better(existing, row)
    return list(merged.values()), sorted(trials)


def aggregate(reports_dir: Path | None = None) -> dict[str, Any]:
    reports_dir = reports_dir or REPORTS_DIR
    rows, trials = collect_rows(reports_dir)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trial_id": trials[-1] if trials else 1,
        "trial_ids": trials,
        "trial_count": len(trials),
        "aggregated_across_trials": True,
        "agent_mode_default": "multi_agent",
        "runs": rows,
        "summary": _summary(rows),
    }
    payload["summary"]["trial_ids"] = trials
    payload["summary"]["trial_count"] = len(trials)
    reports_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, default=str)
    (reports_dir / "latest.json").write_text(text, encoding="utf-8")
    (reports_dir / "latest.md").write_text(_render_markdown(payload), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        type=int,
        default=None,
        help="Copy reports/latest.json to reports/trial<N>.json before aggregating.",
    )
    parser.add_argument("--reports-dir", type=Path, default=None)
    args = parser.parse_args()

    reports_dir = args.reports_dir or REPORTS_DIR
    if args.snapshot is not None:
        dest = snapshot_trial(args.snapshot, reports_dir)
        print(f"snapshot -> {dest}")

    payload = aggregate(reports_dir)
    summary = payload["summary"]
    print(f"trials: {payload['trial_ids']} (n={payload['trial_count']})")
    print(f"rows: {summary['runs']}")
    print(f"trial_agreement_rate: {summary['trial_agreement_rate']}")
    print(f"latest.json -> {reports_dir / 'latest.json'}")
    print(f"latest.md   -> {reports_dir / 'latest.md'}")


if __name__ == "__main__":
    main()
