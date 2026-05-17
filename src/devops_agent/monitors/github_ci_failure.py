from __future__ import annotations

import logging
from devops_agent.monitors.base import BaseMonitor
from devops_agent.event import AgentEvent
from devops_agent.mcp_adapter import MCPAdapter
import time
import datetime

logger = logging.getLogger(__name__)
SKIP_BRANCHES = {"main", "master", "develop", "release"}

class GitHubCIFailureMonitor(BaseMonitor):
    """
        Polls GitHub Actions runs across the org and emits an event for each
        newly failed run on a non-main branch.
    """

    def __init__(self, adapter: MCPAdapter, org: str) -> None:
        self._adapter = adapter
        self._org = org
        self._alerted: dict[str, set[int]] = {}
        self._start_time = time.time()

    def poll(self) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        try:
            resp = self._adapter.run(
                "github", "list_org_repos", {"org": self._org, "limit": 200}, dry_run=False
            )
            repos = resp.get("repos", [])
        except Exception:
            logger.exception("[CIFailureMonitor] Failed to list org repos")
            return events

        for repo in repos:
            repo_full_name: str = repo.get("full_name", "")
            if not repo_full_name:
                continue
            alerted_in_repo = self._alerted.setdefault(repo_full_name, set())

            try:
                runs_resp = self._adapter.run(
                    "github", "get_workflow_runs",
                    {"repo": repo_full_name, "per_page": 10},
                    dry_run=False,
                )
                runs = runs_resp.get("runs", [])
                in_progress_resp = self._adapter.run(
                    "github", "get_workflow_runs",
                    {"repo": repo_full_name, "per_page": 50, "status": "in_progress"},
                    dry_run=False,
                )
                in_progress_shas: set[str] = {
                    r.get("head_sha") for r in in_progress_resp.get("runs", [])
                    if r.get("head_sha")
                }
            except Exception:
                logger.exception("[CIFailureMonitor] Failed to get runs for %s", repo_full_name)
                continue

            for run in runs:
                run_id: int = run.get("id", 0)
                if run_id in alerted_in_repo:
                    continue

                created_at = run.get("created_at")
                if created_at:
                    try:
                        created_at_dt = datetime.datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
                        created_at_ts = created_at_dt.timestamp()
                        if created_at_ts < self._start_time:
                            continue  
                        
                    except Exception:
                        logger.warning("[CIFailureMonitor] Nie można sparsować created_at: %s", created_at)

                status = run.get("status", "")
                conclusion = run.get("conclusion", "")
                branch: str = run.get("head_branch", "")

                if status != "completed" or conclusion != "failure":
                    continue
                
                if branch in SKIP_BRANCHES:
                    continue

                head_sha: str = run.get("head_sha", "")
                if head_sha and head_sha in in_progress_shas:
                    logger.debug(
                        "[CIFailureMonitor] Skipping run#%d — commit %s has run(s) still in-progress",
                        run_id, head_sha[:8],
                    )
                    continue

                if run.get("pull_requests"):
                    logger.debug(
                        "[CIFailureMonitor] Skipping run#%d — linked to PR(s), handled by PRBuildMonitor",
                        run_id,
                    )
                    alerted_in_repo.add(run_id)
                    continue

                if not self._branch_has_open_pr(repo_full_name, branch):
                    logger.info(
                        "[CIFailureMonitor] Skipping run#%d on %s/%s — no open PR for branch",
                        run_id, repo_full_name, branch,
                    )
                    alerted_in_repo.add(run_id)
                    continue

                if self._agent_already_commented(repo_full_name, branch):
                    alerted_in_repo.add(run_id)
                    continue

                alerted_in_repo.add(run_id)
                events.append(AgentEvent(
                    kind="github_ci_failure",
                    title=f"{repo_full_name}: CI failure on {branch} (run #{run_id})",
                    context={
                        "repo_full_name": repo_full_name,
                        "run_id": run_id,
                        "branch": branch,
                        "workflow_name": run.get("name", ""),
                        "run_url": run.get("html_url", ""),
                        "namespace": "iotag-sbx",
                    },
                ))

        return events

    def _branch_has_open_pr(self, repo: str, branch: str) -> bool:
        """Return True if there is at least one open PR whose head branch matches ``branch``."""
        try:
            prs_resp = self._adapter.run(
                "github", "list_pull_requests", {"repo": repo, "state": "open"}, dry_run=False
            )
            return any(
                pr.get("head", {}).get("ref", "") == branch
                for pr in prs_resp.get("pull_requests", [])
            )
        except Exception:
            logger.exception(
                "[CIFailureMonitor] Failed checking open PRs for %s branch %s", repo, branch
            )
        return False

    def _agent_already_commented(self, repo: str, branch: str) -> bool:
        """Check if any open PR for this branch already has an agent comment."""
        try:
            prs_resp = self._adapter.run(
                "github", "list_pull_requests", {"repo": repo, "state": "open"}, dry_run=False
            )
            for pr in prs_resp.get("pull_requests", []):
                if pr.get("head", {}).get("ref", "") != branch:
                    continue
                comments_resp = self._adapter.run(
                    "github", "get_pr_comments",
                    {"repo": repo, "pr_number": pr["number"]},
                    dry_run=False,
                )
                comments = comments_resp.get("comments", [])
                if any("## gh_action_bot" in c.get("body", "").lower() for c in comments):
                    return True
        except Exception:
            logger.exception("[CIFailureMonitor] Failed checking PR comments for %s branch %s", repo, branch)
        return False
