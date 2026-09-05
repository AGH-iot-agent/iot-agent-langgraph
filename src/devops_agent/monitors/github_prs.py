import logging
from devops_agent.monitors.base import BaseMonitor
from devops_agent.event import AgentEvent
from devops_agent.mcp_adapter import MCPAdapter

PR_LABELS = {
    "bug", "infrastructure", "devops", "k8s", "kubernetes",
    "deployment", "crash", "incident", "outage", "ci", "cd",
    "ops", "helm", "docker", "container", "pipeline",
}

INFRA_TITLE_KEYWORDS = {
    "pod", "deploy", "crash", "oom", "k8s", "helm",
    "ci ", "cd ", "pipeline", "container", "docker",
    "node ", "cluster", "namespace", "loki", "prometheus",
}

logger = logging.getLogger(__name__)


def pr_head_sha(pr: dict) -> str:
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    return str(head.get("sha") or pr.get("head_sha") or "")


def workflow_state_for_sha(adapter: MCPAdapter, repo: str, head_sha: str) -> str:
    """Return ``in_progress``, ``failure``, ``success``, or ``none`` for ``head_sha``.

    Used so the agent does not comment on a PR the moment it is opened — CI
    logs and cluster status do not exist yet.
    """
    if not head_sha:
        return "none"

    def _runs(status: str | None = None) -> list[dict]:
        args: dict = {"repo": repo, "per_page": 50}
        if status:
            args["status"] = status
        try:
            resp = adapter.run("github", "get_workflow_runs", args, dry_run=False)
        except Exception:
            logger.exception("[GitHubPRMonitor] get_workflow_runs failed for %s", repo)
            return []
        if resp.get("status") not in ("ok", None):
            return []
        return list(resp.get("runs") or [])

    in_flight = {"in_progress", "queued", "waiting", "requested", "pending"}
    for run in _runs("in_progress") + _runs("queued"):
        if run.get("head_sha") == head_sha and str(run.get("status") or "") in in_flight:
            return "in_progress"

    completed = [
        run for run in _runs()
        if run.get("head_sha") == head_sha and str(run.get("status") or "") == "completed"
    ]
    if not completed:
        # A just-opened PR often has no runs listed yet; wait rather than comment.
        return "in_progress"

    latest = completed[0]
    conclusion = str(latest.get("conclusion") or "")
    if conclusion == "failure":
        return "failure"
    if conclusion in {"success", "skipped", "cancelled", "neutral"}:
        return "success"
    return "none"


class GitHubPRMonitor(BaseMonitor):
    def __init__(self, adapter: MCPAdapter, org: str) -> None:
        self._adapter = adapter
        self._org = org
        self._commented: dict[str, set[int]] = {}

    def poll(self) -> list[AgentEvent]:
        events = []
        try:
            resp = self._adapter.run("github", "list_org_repos", {"org": self._org, "limit": 200}, dry_run=False)
            repos = resp.get("repos_with_prs") or resp.get("repos", [])
            for repo in repos:
                repo_full_name = repo["full_name"]
                prs_resp = self._adapter.run("github", "list_pull_requests", {"repo": repo_full_name, "state": "open"}, dry_run=False)
                prs = prs_resp.get("pull_requests", []) or []
                commented_in_repo = self._commented.setdefault(repo_full_name, set())
                for pr in prs:
                    pr_number = pr.get("number")
                    if pr_number in commented_in_repo:
                        continue
                    comments_resp = self._adapter.run("github", "get_pr_comments", {"repo": repo_full_name, "pr_number": pr_number}, dry_run=False)
                    comments = comments_resp.get("comments", []) if comments_resp.get("status") == "ok" else []
                    found_devops_comment = any("## gh_action_bot" in (c.get("body", "").lower()) for c in comments)
                    if found_devops_comment:
                        commented_in_repo.add(pr_number)
                        continue
                    raw_labels = pr.get("labels") or []
                    labels_set = {
                        lbl.get("name", "").lower() if isinstance(lbl, dict) else str(lbl).lower()
                        for lbl in raw_labels
                    }
                    if "iot-devops-agent" not in labels_set:
                        continue

                    ci_state = workflow_state_for_sha(
                        self._adapter, repo_full_name, pr_head_sha(pr)
                    )
                    if ci_state == "in_progress":
                        logger.info(
                            "[GitHubPRMonitor] Waiting for CI on %s#%s before commenting",
                            repo_full_name, pr_number,
                        )
                        continue
                    if ci_state == "failure":
                        # GitHubPRBuildMonitor owns failed CI (logs + sandbox fix).
                        logger.info(
                            "[GitHubPRMonitor] Skipping %s#%s — CI failed; PRBuildMonitor handles it",
                            repo_full_name, pr_number,
                        )
                        commented_in_repo.add(pr_number)
                        continue

                    title_lower = pr.get("title", "").lower()
                    is_infra = bool(labels_set & PR_LABELS) or any(kw in title_lower for kw in INFRA_TITLE_KEYWORDS)
                    events.append(AgentEvent(
                        kind="github_pr",
                        title=f"{repo_full_name}#{pr_number}: {pr.get('title')}",
                        context={
                            "repo": repo,
                            "pr": pr,
                            "is_infra": is_infra,
                            "repo_full_name": repo_full_name,
                            "pr_number": pr_number,
                            "branch": (pr.get("head") or {}).get("ref", ""),
                            "head_sha": pr_head_sha(pr),
                            "ci_state": ci_state,
                        }
                    ))
                    commented_in_repo.add(pr_number)
        except Exception:
            logger.exception("[GitHubPRMonitor] poll failed")
        return events

