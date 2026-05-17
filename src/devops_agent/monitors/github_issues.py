from __future__ import annotations
import logging
from devops_agent.event import AgentEvent
from devops_agent.monitors.base import BaseMonitor
from devops_agent.mcp_adapter import MCPAdapter

INFRA_LABELS = {
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

class GitHubIssueMonitor(BaseMonitor):
    def __init__(self, adapter: MCPAdapter, org: str) -> None:
        self._adapter = adapter
        self._org = org
        self._commented: dict[str, set[int]] = {}

    def poll(self) -> list[AgentEvent]:
        events = []
        try:
            resp = self._adapter.run("github", "list_org_repos", {"org": self._org, "limit": 200}, dry_run=False)
            repos = resp.get("repos_with_issues") or resp.get("repos", [])
            for repo in repos:
                repo_full_name = repo["full_name"]
                issues_resp = self._adapter.run("github", "list_issues", {"repo": repo_full_name, "state": "open"}, dry_run=False)
                issues = issues_resp.get("issues", []) or []
                commented_in_repo = self._commented.setdefault(repo_full_name, set())
                for issue in issues:
                    issue_number = issue.get("number")
                    if issue_number in commented_in_repo:
                        continue
                    comments_resp = self._adapter.run("github", "get_issue_comments", {"repo": repo_full_name, "issue_number": issue_number}, dry_run=False)
                    comments = comments_resp.get("comments", []) if comments_resp.get("status") == "ok" else []
                    found_devops_comment = any("## gh_action_bot" in (c.get("body", "").lower()) for c in comments)
                    if found_devops_comment:
                        commented_in_repo.add(issue_number)
                        continue

                    raw_labels = issue.get("labels") or []
                    labels_set = {
                        lbl.get("name", "").lower() if isinstance(lbl, dict) else str(lbl).lower()
                        for lbl in raw_labels
                    }

                    if "iot-devops-agent" not in labels_set:
                        continue

                    title_lower = issue.get("title", "").lower()
                    is_infra = bool(labels_set & INFRA_LABELS) or any(kw in title_lower for kw in INFRA_TITLE_KEYWORDS)
                    logger.info("[GitHubIssueMonitor] Detected issue: %s#%d (infra=%s) with labels %s", repo_full_name, issue_number, is_infra, labels_set)
                    events.append(AgentEvent(
                        kind="github_issue",
                        title=f"{repo_full_name}#{issue_number}: {issue.get('title')}",
                        context={
                            "repo": repo,
                            "issue": issue,
                            "is_infra": is_infra,
                            "repo_full_name": repo_full_name,
                            "issue_number": issue_number,
                        }
                    ))
                    commented_in_repo.add(issue_number)
        except Exception:
            logger.exception("[GitHubIssueMonitor] poll failed")
        return events
