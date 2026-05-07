from __future__ import annotations

import difflib
import logging
from typing import Any

from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.state import AgentState
from devops_agent.nodes.pr_creator import _patch_file_content

logger = logging.getLogger(__name__)

_adapter = MCPAdapter()


def pr_fix_commenter_node(state: AgentState) -> AgentState:
    """
        Post a fix suggestion as a comment on an existing PR after a PR build failure.

        This node is used instead of pr_creator_node for ``github_pr_build_failure``
        events — the PR already exists, so we comment rather than opening a new one.

        Comment is posted regardless of whether sandbox validation passed or failed:
        - Passed: comment includes confirmed fix with changed files
        - Failed: comment explains what the agent tried + why validation failed

        The watchdog wraps the final_summary with the ``## gh_action_bot`` header
        before posting, so this node does NOT add the header itself.
    """
    context: dict[str, Any] = state.get("context", {})
    repo: str = context.get("repo_full_name", "")
    pr_number: int = context.get("pr_number", 0)
    branch: str = context.get("branch", "")
    run_url: str = context.get("run_url", "")
    namespace: str = context.get("namespace", "iotag-dev")

    proposal: dict[str, Any] = state.get("ci_fix_proposal") or {}
    validation: dict[str, Any] = state.get("validation_result") or {}

    root_cause: str = proposal.get("root_cause", "unknown — agent could not determine root cause")
    error_message: str = proposal.get("error_message", "")
    description: str = proposal.get("description", "")
    files: list[dict[str, Any]] = proposal.get("files", [])

    dry_run: bool = state.get("dry_run", True)

    # Attempt to create a fix branch PR targeting the failing PR's branch (not main).
    fix_pr_url: str | None = None
    fix_branch: str | None = None
    if files and repo and branch and not dry_run:
        fix_branch, fix_pr_url = _create_fix_pr(repo, branch, files, root_cause, pr_number)
        if fix_pr_url:
            logger.info("[PR_FIX_COMMENTER] Fix PR created: %s", fix_pr_url)
        else:
            logger.warning("[PR_FIX_COMMENTER] Could not create fix PR for %s#%d", repo, pr_number)

    comment_body = _build_comment(
        root_cause, files, run_url, namespace, validation, error_message, description,
        fix_pr_url=fix_pr_url, fix_branch=fix_branch,
    )

    if not repo or not pr_number:
        logger.warning(
            "[PR_FIX_COMMENTER] Missing repo or pr_number — cannot post comment. "
            "repo=%r pr_number=%r", repo, pr_number
        )
        return {**state, "final_summary": comment_body}

    if dry_run:
        logger.info(
            "[PR_FIX_COMMENTER] dry_run=True — would comment on %s#%d", repo, pr_number
        )
        return {**state, "final_summary": f"[DRY RUN] Would post comment on {repo}#{pr_number}:\n\n{comment_body}"}

    result = _adapter.run(
        "github", "create_issue_comment",
        {"repo": repo, "issue_number": pr_number, "body": f"## gh_action_bot\n\n{comment_body}"},
        dry_run=False,
    )

    if result.get("status") != "ok":
        msg = result.get("message", "unknown error")
        logger.error("[PR_FIX_COMMENTER] Failed to post PR comment: %s", msg)
        return {**state, "final_summary": f"Failed to post comment on {repo}#{pr_number}: {msg}"}

    logger.info("[PR_FIX_COMMENTER] Comment posted on %s#%d", repo, pr_number)
    return {**state, "pr_url": fix_pr_url, "final_summary": comment_body}


def _create_fix_pr(
    repo: str,
    branch: str,
    files: list[dict[str, Any]],
    root_cause: str,
    pr_number: int,
) -> tuple[str | None, str | None]:
    """
    Create a fix branch from `branch`, commit all fixed files, and open a PR
    targeting `branch` (not main). Returns (fix_branch_name, pr_url) or (None, None).
    """
    fix_branch = f"fix/agent-ci-{branch[:40]}"

    sha_result = _adapter.run("github", "get_branch_sha", {"repo": repo, "branch": branch}, dry_run=False)
    if sha_result.get("status") != "ok":
        logger.error("[PR_FIX_COMMENTER] Cannot get SHA for %s@%s: %s", repo, branch, sha_result.get("message"))
        return fix_branch, None

    base_sha: str = sha_result["sha"]

    branch_result = _adapter.run(
        "github", "create_branch",
        {"repo": repo, "branch_name": fix_branch, "from_sha": base_sha},
        dry_run=False,
    )
    if branch_result.get("status") not in ("ok",):
        logger.error("[PR_FIX_COMMENTER] Cannot create branch %s: %s", fix_branch, branch_result.get("message"))
        return fix_branch, None

    commit_ok = False
    for file_entry in files:
        path = file_entry.get("path", "")
        original_snippet: str = file_entry.get("original_snippet", "") or ""
        fixed_snippet: str = file_entry.get("fixed_snippet") or file_entry.get("content") or ""
        if not path or not fixed_snippet:
            continue
        content = _patch_file_content(repo, path, branch, original_snippet, fixed_snippet)
        if content is None:
            logger.warning("[PR_FIX_COMMENTER] Skipping %s — patch failed", path)
            continue
        commit_result = _adapter.run(
            "github", "commit_file",
            {
                "repo": repo,
                "path": path,
                "content": content,
                "branch": fix_branch,
                "message": f"fix(agent): {root_cause[:72]}",
            },
            dry_run=False,
        )
        if commit_result.get("status") == "ok":
            commit_ok = True
        else:
            logger.warning("[PR_FIX_COMMENTER] Failed to commit %s: %s", path, commit_result.get("message"))

    if not commit_ok:
        return fix_branch, None

    pr_title = f"fix: agent CI repair for PR #{pr_number} on {branch}"
    pr_body = (
        f"Automated fix for CI failure on PR #{pr_number}.\n\n"
        f"**Root cause:** {root_cause}\n\n"
        f"**Target branch:** `{branch}`\n\n"
        f"Merge this into `{branch}` and re-run CI."
    )
    pr_result = _adapter.run(
        "github", "create_pull_request",
        {"repo": repo, "title": pr_title, "body": pr_body, "branch": fix_branch, "base": branch},
        dry_run=False,
    )
    if pr_result.get("status") != "ok":
        logger.error("[PR_FIX_COMMENTER] Failed to create fix PR: %s", pr_result.get("message"))
        return fix_branch, None

    return fix_branch, pr_result.get("pr_url", "")


def _file_diff(f: dict[str, Any]) -> str:
    path = f["path"]
    original = f.get("original_snippet", "") or f.get("content", "")
    fixed = f.get("fixed_snippet", "") or f.get("content", "")
    if original and fixed and original != fixed:
        diff_str = "".join(difflib.unified_diff(
            original.splitlines(keepends=True),
            fixed.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        ))[:3000]
    else:
        diff_str = fixed[:2000] if fixed else ""
    if not diff_str:
        return ""
    return (
        f"<details>\n<summary><code>{path}</code></summary>\n\n"
        f"```diff\n{diff_str}\n```\n\n</details>\n\n"
    )


def _build_diff_sections(files: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (file_section, diff_sections) markdown strings."""
    if not files:
        return (
            "### Proposed changes\n_No file changes proposed — agent could not determine a safe fix._\n\n",
            "",
        )
    file_list = "\n".join(f"- `{f['path']}`" for f in files)
    diff_sections = "".join(_file_diff(f) for f in files)
    return f"### Proposed changes\n{file_list}\n\n", diff_sections


def _build_next_steps(files: list[dict[str, Any]], fix_pr_url: str | None = None, fix_branch: str | None = None) -> str:
    if not files:
        return (
            "### Next Steps\n\n"
            "The agent could not determine a safe automated fix. "
            "Review the root cause and error message above and apply a manual fix to this branch.\n\n"
        )
    fix_pr_line = ""
    if fix_pr_url and fix_branch:
        fix_pr_line = (
            f"**Option A — Merge agent fix PR:**\n"
            f"The agent opened **[{fix_branch}]({fix_pr_url})** targeting this branch. "
            f"Review, approve, and merge it to resolve the CI failure.\n\n"
            f"**Option B — Apply fix manually:**\n"
        )
    else:
        fix_pr_line = "**Apply fix manually:**\n"
    manual_steps = "\n".join(
        f"   ```\n   # Edit {f['path']} and apply the diff shown above\n   ```"
        for f in files[:2]
    )
    return (
        f"### Next Steps\n\n"
        f"{fix_pr_line}"
        f"{manual_steps}\n"
        f"Then push to this branch and re-run the CI workflow.\n\n"
    )


def _build_comment(
    root_cause: str,
    files: list[dict[str, Any]],
    run_url: str,
    namespace: str,
    validation: dict[str, Any],
    error_message: str = "",
    description: str = "",
    fix_pr_url: str | None = None,
    fix_branch: str | None = None,
) -> str:
    validation_passed = validation.get("passed", False)
    validation_status = "passed" if validation_passed else "failed"
    validation_output = validation.get("output", "")
    validation_errors = "\n".join(validation.get("errors", []))

    error_block = f"**Error message:**\n```\n{error_message}\n```\n\n" if error_message else ""
    desc_block = f"{description}\n\n" if description else ""

    file_section, diff_sections = _build_diff_sections(files)

    validation_block = (
        f"### Validation on `{namespace}`\n"
        f"**Status:** {validation_status}\n\n"
        f"```\n{validation_output}\n{validation_errors}\n```\n"
    )

    if validation_passed:
        action_note = (
            "_Validation passed on `{ns}`. Apply the proposed changes and push to this branch "
            "to resolve the CI failure._".format(ns=namespace)
        )
    else:
        action_note = (
            "_Validation did not pass on `{ns}`. Review the errors above before applying._".format(
                ns=namespace
            )
        )

    next_steps = _build_next_steps(files, fix_pr_url=fix_pr_url, fix_branch=fix_branch)

    fix_pr_banner = ""
    if fix_pr_url and fix_branch:
        fix_pr_banner = (
            f"> 🤖 **Agent opened a fix PR:** [{fix_branch}]({fix_pr_url}) "
            f"— merge it into this branch to resolve the failure.\n\n"
        )

    return (
        f"> **gh_action_bot** detected a CI build failure on this PR and ran an automated analysis.\n\n"
        f"**Failing run:** {run_url}\n\n"
        f"{fix_pr_banner}"
        f"###  Root Cause\n{root_cause}\n\n"
        f"{error_block}"
        f"{desc_block}"
        f"{file_section}"
        f"{diff_sections}"
        f"{validation_block}\n"
        f"{next_steps}"
        f"{action_note}\n\n"
        f"---\n"
        f"*Review before applying. The agent performs dry-run validation but cannot guarantee "
        f"correctness for all edge cases.*"
    )
