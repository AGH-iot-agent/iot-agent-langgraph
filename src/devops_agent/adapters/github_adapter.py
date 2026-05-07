from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import subprocess
import json
import logging

logger = logging.getLogger(__name__)

'''
    Adapter for Github CLI for interacting with all Github-related functionality:
    - repo info (tree, file content, search code, commit history)
    - PR / issue status (list PRs/issues, get details)
    - CI/CD (list workflows, get workflow runs, get job logs)

    This requires gh CLI Tool to be installed. Could be migrated to direct API calls in the future if needed,
    but using CLI for now for simplicity and better error handling.
'''

@dataclass
class GHAdapter:
    def get_issue_comments(self, repo: str, issue_number: int) -> dict[str, Any]:
        cmd = ["gh", "api", f"/repos/{repo}/issues/{issue_number}/comments"]
        logger.debug("[GHAdapter] Getting comments for issue #%d in %s", issue_number, repo)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("[GHAdapter] Failed to get comments for issue #%d in %s: %s", issue_number, repo, result.stderr)
                return {"status": "error", "message": result.stderr}
            comments_json = json.loads(result.stdout)
            logger.debug("[GHAdapter] Got %d comments for issue #%d in %s", len(comments_json), issue_number, repo)
            return {"status": "ok", "comments": comments_json}
        except Exception as e:
            logger.exception("[GHAdapter] Exception occurred while getting comments for issue #%d in %s", issue_number, repo)
            return {"status": "error", "message": str(e)}

    '''
        Repo info:
        - get_repo_tree(repo, ref)
        - get_file_content(repo, path, ref)
        - search_code(repo, query)
        - get_commit_history(repo, path, ref)
        - list_org_repos(org, limit) --- for watchdog, returns repos with open issues
    '''

    def get_repo_tree(self, repo: str, ref: str = "HEAD") -> dict[str, Any]:
        cmd = ["gh", "repo", "view", repo, "--json", "defaultBranchRef"]
        logger.debug("[GHAdapter] Getting repo tree for %s at ref %s", repo, ref)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("[GHAdapter] Failed to get repo info for %s: %s", repo, result.stderr)
                return {"status": "error", "message": result.stderr}

            repo_json = json.loads(result.stdout)
            default_branch = repo_json.get("defaultBranchRef", {}).get("name", "main")
            ref = ref or default_branch

            cmd_tree = ["gh", "api", f"/repos/{repo}/git/trees/{ref}?recursive=1"]
            result_tree = subprocess.run(cmd_tree, capture_output=True, text=True, env=os.environ)
            if result_tree.returncode != 0:
                logger.error("[GHAdapter] Failed to get repo tree for %s at ref %s: %s", repo, ref, result_tree.stderr)
                return {"status": "error", "message": result_tree.stderr}

            tree_json = json.loads(result_tree.stdout)
            logger.debug("[GHAdapter] Successfully retrieved repo tree for %s at ref %s with %d items", repo, ref, len(tree_json.get("tree", [])))

            return {"status": "ok", "tree": tree_json.get("tree", [])}
        except Exception as e:
            logger.exception("[GHAdapter] Exception occurred while getting repo tree for %s at ref %s", repo, ref)
            return {"status": "error", "message": str(e)}


    def _decode_file_content(self, raw: str) -> dict[str, Any]:
        import base64
        try:
            content_json = json.loads(raw)
        except json.JSONDecodeError:
            return {"status": "error", "message": "invalid JSON in response"}
        if content_json.get("encoding") == "base64":
            content = base64.b64decode(content_json.get("content", "")).decode()
            return {"status": "ok", "content": content}
        return {"status": "error", "message": f"unsupported encoding: {content_json.get('encoding')}"}

    def _get_file_at_ref(self, repo: str, path: str, ref: str) -> dict[str, Any]:
        cmd = ["gh", "api", f"/repos/{repo}/contents/{path}?ref={ref}"]
        result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
        if result.returncode != 0:
            return {"status": "error", "message": result.stderr, "not_found": (
                "404" in result.stderr or "No commit found" in result.stderr
            )}
        return self._decode_file_content(result.stdout)

    def get_file_content(self, repo: str, path: str, ref: str = "HEAD") -> dict[str, Any]:
        logger.debug("[GHAdapter] Getting file content for %s/%s at ref %s", repo, path, ref)
        try:
            result = self._get_file_at_ref(repo, path, ref)
            if result.get("status") == "ok":
                return result
            if result.get("not_found") and ref not in ("HEAD", "main", "master"):
                logger.debug("[GHAdapter] ref %s not found for %s/%s — retrying with main", ref, repo, path)
                fallback = self._get_file_at_ref(repo, path, "main")
                if fallback.get("status") == "ok":
                    return fallback
                logger.warning("[GHAdapter] File %s/%s not found on ref %s or main", repo, path, ref)
            elif result.get("not_found"):
                logger.warning("[GHAdapter] Failed to get file content for %s/%s at ref %s: %s", repo, path, ref, result.get("message", "").strip())
            else:
                logger.error("[GHAdapter] Failed to get file content for %s/%s at ref %s: %s", repo, path, ref, result.get("message", "").strip())
            return {"status": "error", "message": result.get("message", "")}

        except Exception as e:
            logger.exception("[GHAdapter] Exception occurred while getting file content for %s/%s at ref %s", repo, path, ref)
            return {"status": "error", "message": str(e)}


    def search_code(self, repo: str, query: str) -> dict[str, Any]:
        cmd = ["gh", "api", f"/search/code?q={query}+repo:{repo}"]
        logger.debug("[GHAdapter] Searching code in %s with query '%s'", repo, query)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("[GHAdapter] Failed to search code in %s with query '%s': %s", repo, query, result.stderr)
                return {"status": "error", "message": result.stderr}

            search_json = json.loads(result.stdout)
            logger.debug("[GHAdapter] Search code in %s with query '%s' returned %d results", repo, query, search_json.get("total_count", 0))
            return {"status": "ok", "results": search_json.get("items", [])}
        except Exception as e:
            logger.exception("[GHAdapter] Exception occurred while searching code in %s with query '%s'", repo, query)
            return {"status": "error", "message": str(e)}


    def get_commit_history(self, repo: str, path: str = "", ref: str = "HEAD", **_extra) -> dict[str, Any]:
        cmd = ["gh", "api", f"/repos/{repo}/commits?path={path}&sha={ref}"]
        logger.debug("[GHAdapter] Getting commit history for %s/%s at ref %s", repo, path, ref)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("[GHAdapter] Failed to get commit history for %s/%s at ref %s: %s", repo, path, ref, result.stderr)
                return {"status": "error", "message": result.stderr}
            commits_json = json.loads(result.stdout)
            logger.debug("[GHAdapter] Successfully got commit history for %s/%s at ref %s", repo, path, ref)
            return {"status": "ok", "commits": commits_json}
        except Exception as e:
            logger.exception("[GHAdapter] Exception occurred while getting commit history for %s/%s at ref %s", repo, path, ref)
            return {"status": "error", "message": str(e)}


    def list_org_repos(self, org: str, limit: int = 100) -> dict[str, Any]:
        cmd = ["gh", "api", f"/orgs/{org}/repos"]
        logger.debug("[GHAdapter] Listing repos for org %s with limit %d", org, limit)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("[GHAdapter] Failed to list repos for org %s with limit %d: %s", org, limit, result.stderr)
                return {"status": "error", "message": result.stderr}
            repos_json = json.loads(result.stdout)
            logger.debug("[GHAdapter] Successfully listed repos for org %s with limit %d: %d repos", org, limit, len(repos_json))
            return {"status": "ok", "repos": repos_json}
        except Exception as e:
            logger.exception("[GHAdapter] Exception occurred while listing repos for org %s with limit %d", org, limit)
            return {"status": "error", "message": str(e)}

    '''
        PR / issue status:
        - list_pull_requests(repo, state)
        - get_pull_request(repo, pr_number)
        - list_issues(repo, state)
        - get_issue(repo, issue_number)
    '''

    def list_pull_requests(self, repo: str, state: str = "open") -> dict[str, Any]:
        cmd = ["gh", "api", f"/repos/{repo}/pulls?state={state}"]
        logger.debug("[GHAdapter] Listing pull requests for %s with state '%s'", repo, state)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("[GHAdapter] Failed to list pull requests for %s with state '%s': %s", repo, state, result.stderr)
                return {"status": "error", "message": result.stderr}
            prs_json = json.loads(result.stdout)
            logger.debug("[GHAdapter] Successfully listed pull requests for %s with state '%s': %d PRs", repo, state, len(prs_json))
            return {"status": "ok", "pull_requests": prs_json}
        except Exception as e:
            logger.exception("[GHAdapter] Exception occurred while listing pull requests for %s with state '%s'", repo, state)
            return {"status": "error", "message": str(e)}


    def get_pull_request(self, repo: str, pr_number: int) -> dict[str, Any]:
        cmd = ["gh", "api", f"/repos/{repo}/pulls/{pr_number}"]
        logger.debug("[GHAdapter] Getting pull request #%d for %s", pr_number, repo)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("[GHAdapter] Failed to get pull request #%d for %s: %s", pr_number, repo, result.stderr)
                return {"status": "error", "message": result.stderr}
            pr_json = json.loads(result.stdout)
            logger.debug("[GHAdapter] Successfully got pull request #%d for %s", pr_number, repo)
            return {"status": "ok", "pull_request": pr_json}
        except Exception as e:
            logger.exception("[GHAdapter] Exception occurred while getting pull request #%d for %s", pr_number, repo)
            return {"status": "error", "message": str(e)}


    def list_issues(self, repo: str, state: str = "open") -> dict[str, Any]:
        cmd = ["gh", "api", f"/repos/{repo}/issues?state={state}"]
        logger.debug("[GHAdapter] Listing issues for %s with state '%s'", repo, state)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("[GHAdapter] Failed to list issues for %s with state '%s': %s", repo, state, result.stderr)
                return {"status": "error", "message": result.stderr}
            issues_json = json.loads(result.stdout)
            logger.debug("[GHAdapter] Successfully listed issues for %s with state '%s': %d issues", repo, state, len(issues_json))
            return {"status": "ok", "issues": issues_json}
        except Exception as e:
            logger.exception("[GHAdapter] Exception occurred while listing issues for %s with state '%s'", repo, state)
            return {"status": "error", "message": str(e)}
    

    def get_issue(self, repo: str, issue_number: int) -> dict[str, Any]:
        cmd = ["gh", "api", f"/repos/{repo}/issues/{issue_number}"]
        logger.debug("[GHAdapter] Getting issue #%d for %s", issue_number, repo)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("[GHAdapter] Failed to get issue #%d for %s: %s", issue_number, repo, result.stderr)
                return {"status": "error", "message": result.stderr}
            issue_json = json.loads(result.stdout)
            logger.debug("[GHAdapter] Successfully got issue #%d for %s", issue_number, repo)
            return {"status": "ok", "issue": issue_json}
        except Exception as e:
            logger.exception("[GHAdapter] Exception occurred while getting issue #%d for %s", issue_number, repo)
            return {"status": "error", "message": str(e)}


    def create_issue_comment(
        self,
        repo: str,
        issue_number: int,
        body: str,
    ) -> dict[str, Any]:
        cmd = ["gh", "api", f"/repos/{repo}/issues/{issue_number}/comments", "--method", "POST", "--input", "-"]
        logger.debug("[GHAdapter] Creating comment on issue #%d for %s", issue_number, repo)
        try:
            result = subprocess.run(cmd, input=json.dumps({"body": body}), capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("[GHAdapter] Failed to create comment on issue #%d for %s: %s", issue_number, repo, result.stderr)
                return {"status": "error", "message": result.stderr}
            logger.debug("[GHAdapter] Successfully created comment on issue #%d for %s", issue_number, repo)
            return {"status": "ok"}
        except Exception as e:
            logger.exception("[GHAdapter] Exception occurred while creating comment on issue #%d for %s", issue_number, repo)
            return {"status": "error", "message": str(e)}

    '''        
        CI/CD:
        - list_workflows(repo)
        - get_workflow_runs(repo, workflow_id, status)
        - get_job_logs(repo, run_id)
    '''

    def list_workflows(self, repo: str) -> dict[str, Any]:
        cmd = ["gh", "api", f"/repos/{repo}/actions/workflows"]
        logger.debug("[GHAdapter] Listing workflows for %s", repo)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("[GHAdapter] Failed to list workflows for %s: %s", repo, result.stderr)
                return {"status": "error", "message": result.stderr}
            workflows_json = json.loads(result.stdout)
            logger.debug("[GHAdapter] Successfully listed workflows for %s: %d workflows", repo, len(workflows_json.get("workflows", [])))
            return {"status": "ok", "workflows": workflows_json.get("workflows", [])}
        except Exception as e:
            logger.exception("[GHAdapter] Exception occurred while listing workflows for %s", repo)
            return {"status": "error", "message": str(e)}


    def get_workflow_runs(self, repo: str, per_page: int = 10, status: str = "") -> dict[str, Any]:
        url = f"/repos/{repo}/actions/runs?per_page={per_page}"
        if status:
            url += f"&status={status}"
        cmd = ["gh", "api", url]
        logger.debug("[GHAdapter] Getting workflow runs for %s per_page=%d status='%s'", repo, per_page, status)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                logger.error("[GHAdapter] Failed to get workflow runs for %s: %s", repo, result.stderr)
                return {"status": "error", "message": result.stderr}
            runs_json = json.loads(result.stdout)
            logger.debug("[GHAdapter] Got %d workflow runs for %s", len(runs_json.get("workflow_runs", [])), repo)
            return {"status": "ok", "runs": runs_json.get("workflow_runs", [])}
        except Exception as e:
            logger.exception("[GHAdapter] Exception getting workflow runs for %s", repo)
            return {"status": "error", "message": str(e)}


    def get_job_logs(self, repo: str, run_id: int, job_id: int = 0) -> dict[str, Any]:
        jobs_cmd = ["gh", "api", f"/repos/{repo}/actions/runs/{run_id}/jobs"]
        logger.debug("[GHAdapter] Getting job logs for %s run %d job %d", repo, run_id, job_id)
        try:
            jobs_result = subprocess.run(jobs_cmd, capture_output=True, text=True, env=os.environ)
            if jobs_result.returncode != 0:
                return {"status": "error", "message": jobs_result.stderr}
            jobs_json = json.loads(jobs_result.stdout)
            jobs = jobs_json.get("jobs", [])
            target = next((j for j in jobs if j.get("conclusion") == "failure"), jobs[job_id] if jobs else None)
            if not target:
                return {"status": "ok", "logs": "", "message": "No jobs found"}
            actual_job_id = target["id"]
            logs_cmd = ["gh", "api", f"/repos/{repo}/actions/jobs/{actual_job_id}/logs"]
            logs_result = subprocess.run(logs_cmd, capture_output=True, text=True, env=os.environ)
            logger.debug("[GHAdapter] Got job logs for %s run %d job %d", repo, run_id, actual_job_id)
            return {"status": "ok", "job": target, "logs": logs_result.stdout[-8000:]}  # cap at 8KB
        except Exception as e:
            logger.exception("[GHAdapter] Exception getting job logs for %s run %d", repo, run_id)
            return {"status": "error", "message": str(e)}


    def get_pr_comments(self, repo: str, pr_number: int) -> dict[str, Any]:
        cmd = ["gh", "api", f"/repos/{repo}/issues/{pr_number}/comments"]
        logger.debug("[GHAdapter] Getting PR comments for %s#%d", repo, pr_number)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                return {"status": "error", "message": result.stderr}
            return {"status": "ok", "comments": json.loads(result.stdout)}
        except Exception as e:
            logger.exception("[GHAdapter] Exception getting PR comments for %s#%d", repo, pr_number)
            return {"status": "error", "message": str(e)}


    def get_branch_sha(self, repo: str, branch: str) -> dict[str, Any]:
        """Return the HEAD SHA for a given branch."""
        cmd = ["gh", "api", f"/repos/{repo}/git/refs/heads/{branch}"]
        logger.debug("[GHAdapter] Getting SHA for %s@%s", repo, branch)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                return {"status": "error", "message": result.stderr}
            data = json.loads(result.stdout)
            # API may return a list (when prefix matches multiple refs) or single object
            if isinstance(data, list):
                data = data[0]
            sha = data.get("object", {}).get("sha", "")
            if not sha:
                return {"status": "error", "message": "SHA not found in ref response"}
            return {"status": "ok", "sha": sha}
        except Exception as e:
            logger.exception("[GHAdapter] Exception getting branch SHA for %s@%s", repo, branch)
            return {"status": "error", "message": str(e)}

    def create_branch(self, repo: str, branch_name: str, from_sha: str) -> dict[str, Any]:
        """Create a new branch from an existing commit SHA."""
        payload = json.dumps({"ref": f"refs/heads/{branch_name}", "sha": from_sha})
        cmd = ["gh", "api", f"/repos/{repo}/git/refs", "--method", "POST", "--input", "-"]
        logger.info("[GHAdapter] Creating branch %s on %s from %s", branch_name, repo, from_sha[:12])
        try:
            result = subprocess.run(cmd, input=payload, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                # 422 = branch already exists — treat as ok
                if "already exists" in result.stderr or "422" in result.stderr:
                    logger.warning("[GHAdapter] Branch %s already exists on %s", branch_name, repo)
                    return {"status": "ok", "message": "branch already exists"}
                return {"status": "error", "message": result.stderr}
            return {"status": "ok"}
        except Exception as e:
            logger.exception("[GHAdapter] Exception creating branch %s on %s", branch_name, repo)
            return {"status": "error", "message": str(e)}

    def commit_file(
        self,
        repo: str,
        path: str,
        content: str,
        branch: str,
        message: str,
        sha: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a file on a branch via the Contents API."""
        import base64
        encoded = base64.b64encode(content.encode()).decode()
        payload: dict[str, Any] = {"message": message, "content": encoded, "branch": branch}
        if sha:
            payload["sha"] = sha

        # Fetch current file SHA if not provided (needed for updates)
        if not sha:
            check = self.get_file_content(repo, path, branch)
            if check.get("status") == "ok":
                # File exists — get its blob sha via the raw API
                check_cmd = ["gh", "api", f"/repos/{repo}/contents/{path}?ref={branch}"]
                try:
                    r = subprocess.run(check_cmd, capture_output=True, text=True, env=os.environ)
                    if r.returncode == 0:
                        file_json = json.loads(r.stdout)
                        payload["sha"] = file_json.get("sha", "")
                except Exception:
                    pass

        cmd = ["gh", "api", f"/repos/{repo}/contents/{path}", "--method", "PUT", "--input", "-"]
        logger.info("[GHAdapter] Committing %s on %s@%s", path, repo, branch)
        try:
            result = subprocess.run(
                cmd, input=json.dumps(payload), capture_output=True, text=True, env=os.environ
            )
            if result.returncode != 0:
                return {"status": "error", "message": result.stderr}
            return {"status": "ok"}
        except Exception as e:
            logger.exception("[GHAdapter] Exception committing %s on %s", path, repo)
            return {"status": "error", "message": str(e)}

    def create_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        branch: str,
        base: str = "main",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        if dry_run:
            return {"status": "dry_run", "message": f"[DRY RUN] Would open PR '{title}' on {repo} ({branch} → {base})"}
        payload = json.dumps({"title": title, "body": body, "head": branch, "base": base})
        cmd = ["gh", "api", f"/repos/{repo}/pulls", "--method", "POST", "--input", "-"]
        logger.info("[GHAdapter] Creating PR '%s' on %s (%s → %s)", title, repo, branch, base)
        try:
            result = subprocess.run(cmd, input=payload, capture_output=True, text=True, env=os.environ)
            if result.returncode != 0:
                return {"status": "error", "message": result.stderr}
            pr_json = json.loads(result.stdout)
            return {"status": "ok", "pr_url": pr_json.get("html_url", "")}
        except Exception as e:
            logger.exception("[GHAdapter] Exception creating PR for %s", repo)
            return {"status": "error", "message": str(e)}


    def run(self, action: str, args: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        if dry_run:
            return {"status": "ok", "dry_run": True}

        if action == "get_repo_tree":
            return self.get_repo_tree(args["repo"], args.get("ref", "HEAD"))
        if action == "get_file_content":
            return self.get_file_content(args["repo"], args["path"], args.get("ref", "HEAD"))
        if action == "search_code":
            return self.search_code(args["repo"], args["query"])
        if action == "get_commit_history":
            return self.get_commit_history(args["repo"], args["path"], args.get("ref", "HEAD"))
        if action == "list_org_repos":
            return self.list_org_repos(args["org"], args.get("limit", 100))

        if action == "list_pull_requests":
            return self.list_pull_requests(args["repo"], args.get("state", "open"))
        if action == "get_pull_request":
            return self.get_pull_request(args["repo"], args["pr_number"])
        if action == "list_issues":
            return self.list_issues(args["repo"], args.get("state", "open"))
        if action == "get_issue":
            return self.get_issue(args["repo"], args["issue_number"])
        if action == "create_issue_comment":
            return self.create_issue_comment(args["repo"], args["issue_number"], args["body"])
        if action == "get_issue_comments":
            return self.get_issue_comments(args["repo"], args["issue_number"])

        if action == "list_workflows":
            return self.list_workflows(args["repo"])
        if action == "get_workflow_runs":
            return self.get_workflow_runs(args["repo"], args.get("per_page", 10), args.get("status", ""))
        if action == "get_job_logs":
            return self.get_job_logs(args["repo"], args["run_id"], args.get("job_id", 0))
        if action == "get_pr_comments":
            return self.get_pr_comments(args["repo"], args["pr_number"])
        if action == "create_pull_request":
            return self.create_pull_request(
                args["repo"], args["title"], args["body"],
                args["branch"], args.get("base", "main"), dry_run,
            )
        if action == "get_branch_sha":
            return self.get_branch_sha(args["repo"], args["branch"])
        if action == "create_branch":
            return self.create_branch(args["repo"], args["branch_name"], args["from_sha"])
        if action == "commit_file":
            return self.commit_file(
                args["repo"], args["path"], args["content"],
                args["branch"], args["message"], args.get("sha"),
            )
        return {"status": "error", "message": f"Unknown action: {action}"}