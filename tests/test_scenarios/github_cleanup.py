"""Shared GitHub leftover cleanup for scenario tests (issues / PRs)."""
from __future__ import annotations

import subprocess
from typing import Any

from tests.test_scenarios.github_tokens import github_setup_env


def close_issue(adapter: Any, repo: str, issue_number: int) -> bool:
    result = adapter.run(
        "github",
        "update_issue",
        {
            "repo": repo,
            "issue_number": issue_number,
            "state": "closed",
        },
    )
    return str(result.get("status") or "") == "ok"


def close_pr(repo: str, pr_number: int) -> bool:
    completed = subprocess.run(
        ["gh", "pr", "close", str(pr_number), "--repo", repo, "--delete-branch"],
        text=True,
        capture_output=True,
        check=False,
        env=github_setup_env(),
    )
    return completed.returncode == 0
