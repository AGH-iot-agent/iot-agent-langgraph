from __future__ import annotations

import logging
from devops_agent.monitors.base import BaseMonitor
from devops_agent.event import AgentEvent
from devops_agent.mcp_adapter import MCPAdapter

logger = logging.getLogger(__name__)
SKIP_BRANCHES = {"main", "master", "develop", "release"}


class GitHubPRBuildMonitor(BaseMonitor):
    """
        Detects failed CI builds that are linked to an open PR and emits
        ``github_pr_build_failure`` events so the agent can suggest fixes
        directly on the PR thread.

        Difference from GitHubCIFailureMonitor:
        - Looks only at runs that have ``pull_requests`` populated (GitHub API
        sets this field when a workflow was triggered by a pull_request event).
        - Validates on ``iotag-dev`` (not sbx) — faster feedback for in-review code.
        - Uses ``pr_fix_commenter_node`` instead of ``pr_creator_node`` — comments
        on the existing PR rather than opening a new one.
    """

    def __init__(self, adapter: MCPAdapter, org: str) -> None:
        self._adapter = adapter
        self._org = org
        self._alerted: dict[str, set[int]] = {}

    def poll(self) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        try:
            resp = self._adapter.run(
                "github", "list_org_repos", {"org": self._org, "limit": 200}, dry_run=False
            )
            repos = resp.get("repos", [])
        except Exception:
            logger.exception("[PRBuildMonitor] Failed to list org repos")
            return events

        for repo in repos:
            repo_full_name: str = repo.get("full_name", "")
            if not repo_full_name:
                continue
            alerted_in_repo = self._alerted.setdefault(repo_full_name, set())

            try:
                runs_resp = self._adapter.run(
                    "github", "get_workflow_runs",
                    {"repo": repo_full_name, "per_page": 20},
                    dry_run=False,
                )
                runs = runs_resp.get("runs", [])
            except Exception:
                logger.exception("[PRBuildMonitor] Failed to get runs for %s", repo_full_name)
                continue

            for run in runs:
                run_id: int = run.get("id", 0)
                if run_id in alerted_in_repo:
                    continue

                if run.get("status") != "completed" or run.get("conclusion") != "failure":
                    continue

                linked_prs: list[dict] = run.get("pull_requests") or []
                if not linked_prs:
                    continue

                head_sha: str = run.get("head_sha", "")
                if head_sha:
                    pending = [
                        r for r in runs
                        if r.get("head_sha") == head_sha
                        and r.get("id") != run_id
                        and r.get("status") not in ("completed",)
                    ]
                    if pending:
                        logger.debug(
                            "[PRBuildMonitor] Skipping run#%d — %d run(s) for commit %s still pending",
                            run_id, len(pending), head_sha[:8],
                        )
                        continue 

                pr_number: int = linked_prs[0].get("number", 0)
                branch: str = run.get("head_branch", "")

                pr_base_branch: str = linked_prs[0].get("base", {}).get("ref", "") or ""
                if pr_base_branch and pr_base_branch not in ("main", "master", "develop"):
                    logger.debug(
                        "[PRBuildMonitor] Skipping run#%d — PR#%d targets '%s', not main/develop",
                        run_id, pr_number, pr_base_branch,
                    )
                    alerted_in_repo.add(run_id)
                    continue

                if branch in SKIP_BRANCHES:
                    alerted_in_repo.add(run_id)
                    continue

                if self._agent_already_commented(repo_full_name, pr_number):
                    alerted_in_repo.add(run_id)
                    continue

                alerted_in_repo.add(run_id)
                events.append(AgentEvent(
                    kind="github_pr_build_failure",
                    title=f"{repo_full_name} PR#{pr_number}: build failed on {branch} (run #{run_id})",
                    context={
                        "repo_full_name": repo_full_name,
                        "run_id": run_id,
                        "pr_number": pr_number,
                        "branch": branch,
                        "workflow_name": run.get("name", ""),
                        "run_url": run.get("html_url", ""),
                        "namespace": "iotag-dev",
                    },
                ))
                logger.info(
                    "[PRBuildMonitor] Emitting event: %s PR#%d run#%d branch=%s",
                    repo_full_name, pr_number, run_id, branch,
                )

        return events

    def _agent_already_commented(self, repo: str, pr_number: int) -> bool:
        """
            Return True if gh_action_bot already left a *full* analysis comment on this PR.

            A full comment (from pr_fix_commenter) contains the analysis block header.
            A minimal comment (from the old ci_fixer fallback) does not — so we allow
            re-processing when only the old short comment exists.
        """
        try:
            comments_resp = self._adapter.run(
                "github", "get_pr_comments",
                {"repo": repo, "pr_number": pr_number},
                dry_run=False,
            )
            if comments_resp.get("status") != "ok":
                return False
            return any(
                "detected a ci build failure" in c.get("body", "").lower()
                for c in comments_resp.get("comments", [])
            )
        except Exception:
            logger.exception(
                "[PRBuildMonitor] Failed checking PR comments for %s#%d", repo, pr_number
            )
        return False
