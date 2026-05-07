from __future__ import annotations

import difflib
import logging
from typing import Any

from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.state import AgentState

logger = logging.getLogger(__name__)

_adapter = MCPAdapter()


def _patch_file_content(
    repo: str,
    path: str,
    branch: str,
    original_snippet: str,
    fixed_snippet: str,
) -> str | None:
    """
        Fetch the current file from GitHub, apply a snippet-level patch, and return
        the full patched content.  Returns None if the file could not be fetched.

        Patching strategy:
        - If original_snippet is non-empty and found verbatim in the file → replace only that part.
        - If original_snippet is non-empty but not found (whitespace drift) → try a
        stripped-line match as a best-effort fallback.
        - If original_snippet is empty (LLM only provided fixed_snippet) → return fixed_snippet
        as-is only when the file does not exist yet; otherwise skip to avoid clobbering.
    """
    current_result = _adapter.run("github", "get_file_content", {"repo": repo, "path": path, "ref": branch}, dry_run=False)
    if current_result.get("status") != "ok":
        return fixed_snippet if fixed_snippet else None

    current_content: str = current_result.get("content", "")

    if not original_snippet:
        logger.warning("[PR_CREATOR] No original_snippet for %s — skipping patch to protect file", path)
        return None

    if original_snippet in current_content:
        return current_content.replace(original_snippet, fixed_snippet, 1)

    orig_lines = [l.rstrip() for l in original_snippet.splitlines()]
    curr_lines = current_content.splitlines()
    for i in range(len(curr_lines) - len(orig_lines) + 1):
        window = [l.rstrip() for l in curr_lines[i : i + len(orig_lines)]]
        if window == orig_lines:
            patched = curr_lines[:i] + fixed_snippet.splitlines() + curr_lines[i + len(orig_lines):]
            return "\n".join(patched) + ("\n" if current_content.endswith("\n") else "")

    logger.warning(
        "[PR_CREATOR] original_snippet not found in %s — cannot patch safely, skipping", path
    )
    return None


def pr_creator_node(state: AgentState) -> AgentState:
    """
        Create a GitHub PR with the validated CI fix.

        Steps:
        1. Fetch the HEAD SHA of the failing branch (base for fix branch)
        2. Create a new fix branch via GitHub Contents API (no local git needed)
        3. Commit each changed file onto that branch
        4. Open a PR via GitHub API with a rich body
    """
    context: dict[str, Any] = state.get("context", {})
    repo: str = context.get("repo_full_name", "")
    branch: str = context.get("branch", "main")
    run_url: str = context.get("run_url", "")
    proposal: dict[str, Any] = state.get("ci_fix_proposal") or {}
    validation: dict[str, Any] = state.get("validation_result") or {}

    root_cause: str = proposal.get("root_cause", "unknown")
    error_message: str = proposal.get("error_message", "")
    description: str = proposal.get("description", "")
    files: list[dict[str, Any]] = proposal.get("files", [])

    if not repo:
        return {**state, "final_summary": "PR not created: missing repo_full_name in context."}

    if not files:
        return {**state, "final_summary": "No files to commit — PR not created."}

    fix_branch = f"fix/agent-ci-{branch[:40]}"
    pr_title = f"fix: agent CI repair on {branch}"
    pr_body = _build_pr_body(root_cause, files, run_url, validation, error_message, description)

    dry_run: bool = state.get("dry_run", True)

    if dry_run:
        return {
            **state,
            "pr_url": None,
            "final_summary": (
                f"[DRY RUN] Would create PR '{pr_title}' on {repo}\n"
                f"Branch: {fix_branch} → main\n"
                f"Root cause: {root_cause}\n"
                f"Files: {[f['path'] for f in files]}"
            ),
        }

    sha_result = _adapter.run("github", "get_branch_sha", {"repo": repo, "branch": branch}, dry_run=False)
    if sha_result.get("status") != "ok":
        logger.error("[PR_CREATOR] Failed to get SHA for %s@%s: %s", repo, branch, sha_result.get("message"))
        sha_result = _adapter.run("github", "get_branch_sha", {"repo": repo, "branch": "main"}, dry_run=False)
        if sha_result.get("status") != "ok":
            return {**state, "final_summary": f"PR not created: cannot get branch SHA — {sha_result.get('message')}"}

    base_sha: str = sha_result["sha"]

    branch_result = _adapter.run(
        "github", "create_branch",
        {"repo": repo, "branch_name": fix_branch, "from_sha": base_sha},
        dry_run=False,
    )
    if branch_result.get("status") not in ("ok",):
        return {**state, "final_summary": f"PR not created: cannot create branch — {branch_result.get('message')}"}

    commit_errors: list[str] = []
    for file_entry in files:
        path = file_entry.get("path", "")
        original_snippet: str = file_entry.get("original_snippet", "") or ""
        fixed_snippet: str = file_entry.get("fixed_snippet") or file_entry.get("content") or ""

        if not path or not fixed_snippet:
            logger.warning("[PR_CREATOR] Skipping empty file entry: %s", path)
            continue

        content = _patch_file_content(repo, path, branch, original_snippet, fixed_snippet)
        if content is None:
            commit_errors.append(f"{path}: could not apply patch (original snippet not found or no content)")
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
        if commit_result.get("status") != "ok":
            msg = commit_result.get("message", "unknown error")
            logger.error("[PR_CREATOR] Failed to commit %s: %s", path, msg)
            commit_errors.append(f"{path}: {msg}")

    if commit_errors and len(commit_errors) == len(files):
        return {
            **state,
            "pr_url": None,
            "final_summary": f"PR not created: all file commits failed:\n" + "\n".join(commit_errors),
        }

    pr_result = _adapter.run(
        "github", "create_pull_request",
        {
            "repo": repo,
            "title": pr_title,
            "body": pr_body,
            "branch": fix_branch,
            "base": "main",
        },
        dry_run=False,
    )

    if pr_result.get("status") != "ok":
        msg = pr_result.get("message", "unknown error")
        logger.error("[PR_CREATOR] Failed to create PR: %s", msg)
        return {
            **state,
            "pr_url": None,
            "final_summary": f"PR creation failed: {msg}\nRoot cause: {root_cause}",
        }

    pr_url: str = pr_result.get("pr_url", "")
    logger.info("[PR_CREATOR] PR created: %s", pr_url)

    warn_block = ""
    if commit_errors:
        warn_block = f"\nPartial commit errors:\n" + "\n".join(commit_errors)

    return {
        **state,
        "pr_url": pr_url,
        "final_summary": (
            f"PR created: {pr_url}\n"
            f"Root cause: {root_cause}\n"
            f"Files changed: {[f['path'] for f in files]}\n"
            f"Validation: {'passed' if validation.get('passed') else 'failed'}"
            f"{warn_block}"
        ),
    }


def _build_pr_body(
    root_cause: str,
    files: list[dict[str, Any]],
    run_url: str,
    validation: dict[str, Any],
    error_message: str = "",
    description: str = "",
) -> str:
    validation_status = "passed" if validation.get("passed") else "failed ❌"
    validation_output = validation.get("output", "")
    validation_errors = "\n".join(validation.get("errors", []))

    error_block = ""
    if error_message:
        error_block = f"**Error message:**\n```\n{error_message}\n```\n\n"

    desc_block = f"{description}\n\n" if description else ""

    file_list = "\n".join(f"- `{f['path']}`" for f in files)

    diff_sections = ""
    for f in files:
        path = f["path"]
        original = f.get("original_snippet", "") or ""
        fixed = f.get("fixed_snippet", "") or f.get("content", "")
        if original and fixed and original != fixed:
            diff_lines = list(difflib.unified_diff(
                original.splitlines(keepends=True),
                fixed.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            ))
            diff_str = "".join(diff_lines)[:3000]
        else:
            diff_str = fixed[:2000] if fixed else ""
        if diff_str:
            diff_sections += (
                f"<details>\n<summary><code>{path}</code></summary>\n\n"
                f"```diff\n{diff_str}\n```\n\n</details>\n\n"
            )

    return (
        f"## gh_action_bot — Automated CI Fix\n\n"
        f"> Automatically generated after detecting a CI failure.\n\n"
        f"**Failing run:** {run_url}\n\n"
        f"### Root Cause\n{root_cause}\n\n"
        f"{error_block}"
        f"{desc_block}"
        f"### Changed Files\n{file_list}\n\n"
        f"{diff_sections}"
        f"### Validation (iotag-sbx dry-run)\n"
        f"Status: {validation_status}\n\n"
        f"```\n{validation_output}\n{validation_errors}\n```\n\n"
        f"### Next Steps\n"
        f"1. Review the diff in each `<details>` block above\n"
        f"2. If the fix looks correct, approve and merge\n"
        f"3. If validation failed, check the error output and apply manually:\n"
        f"   ```sh\n"
        f"   git fetch origin {files[0]['path'].split('/')[0] if files else 'fix-branch'}\n"
        f"   # Apply changes from the diff above\n"
        f"   ```\n\n"
        f"---\n"
        f"*The agent performs server-side dry-run validation but cannot guarantee correctness "
        f"for all edge cases. Review carefully before merging.*"
    )



def _build_pr_body(
    root_cause: str,
    files: list[dict[str, Any]],
    run_url: str,
    validation: dict[str, Any],
    error_message: str = "",
    description: str = "",
) -> str:
    validation_status = "passed" if validation.get("passed") else "failed"
    validation_output = validation.get("output", "")
    validation_errors = "\n".join(validation.get("errors", []))

    error_block = ""
    if error_message:
        error_block = f"**Error message:**\n```\n{error_message}\n```\n\n"

    desc_block = f"{description}\n\n" if description else ""

    file_list = "\n".join(f"- `{f['path']}`" for f in files)

    diff_sections = ""
    for f in files:
        path = f["path"]
        original = f.get("original_snippet", "") or f.get("content", "")
        fixed = f.get("fixed_snippet", "") or f.get("content", "")
        if original and fixed and original != fixed:
            diff_lines = list(difflib.unified_diff(
                original.splitlines(keepends=True),
                fixed.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            ))
            diff_str = "".join(diff_lines)[:3000]
        else:
            diff_str = fixed[:2000] if fixed else ""
        if diff_str:
            diff_sections += (
                f"<details>\n<summary><code>{path}</code></summary>\n\n"
                f"```diff\n{diff_str}\n```\n\n</details>\n\n"
            )

    return (
        f"## gh_action_bot — CI Fix\n\n"
        f"> Automatically generated by gh_action_bot after detecting a CI failure.\n\n"
        f"**Failing run:** {run_url}\n"
        f"**Root cause:** {root_cause}\n\n"
        f"{error_block}"
        f"{desc_block}"
        f"### Changed files\n{file_list}\n\n"
        f"{diff_sections}"
        f"### Validation (iotag-sbx)\n"
        f"Status: {validation_status}\n\n"
        f"```\n{validation_output}\n{validation_errors}\n```\n\n"
        f"---\n"
        f"*Review the diff carefully before merging. The agent performs server-side dry-run validation "
        f"but cannot guarantee correctness for all edge cases.*"
    )
