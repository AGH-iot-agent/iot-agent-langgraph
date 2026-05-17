from __future__ import annotations

import difflib
import logging
from datetime import datetime, timezone
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

        For github_pr_build_failure: fix branch is created from the PR branch and
        the PR targets the PR branch (not main), so the fix lands back on the
        in-review branch and triggers a new build.

        For github_ci_failure: fix branch is created from the failing branch and
        the PR targets main (default behaviour).
    """
    context: dict[str, Any] = state.get("context", {})
    event_kind: str = state.get("event_kind", "")
    repo: str = context.get("repo_full_name", "")
    branch: str = context.get("branch", "main")
    run_url: str = context.get("run_url", "")
    proposal: dict[str, Any] = state.get("ci_fix_proposal") or {}
    validation: dict[str, Any] = state.get("validation_result") or {}
    validation_history: list[dict[str, Any]] = list(state.get("validation_history") or [])
    fix_attempt: int = int(state.get("fix_attempt", 0))
    max_fix_attempts: int = int(state.get("max_fix_attempts", 3))

    root_cause: str = proposal.get("root_cause", "unknown")
    error_message: str = proposal.get("error_message", "")
    description: str = proposal.get("description", "")
    files: list[dict[str, Any]] = proposal.get("files", [])

    if not repo:
        return {**state, "final_summary": "PR not created: missing repo_full_name in context."}

    if not files:
        return {**state, "final_summary": "No files to commit — PR not created."}

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    # Determine fix branch name and PR target branch based on event kind
    if event_kind == "github_pr_build_failure":
        pr_number: int = context.get("pr_number", 0)
        fix_branch = f"fix/PR-{pr_number}-{ts}"
        pr_base = branch          # PR targets the PR branch, not main
        pr_title = f"fix: agent PR#{pr_number} CI repair on {branch}"
    else:
        fix_branch = f"fix/agent-ci-{branch[:40]}-{ts}"
        pr_base = "main"
        pr_title = f"fix: agent CI repair on {branch}"

    pr_body = _build_pr_body(
        root_cause,
        _enrich_files_with_originals(files, repo, branch, list(state.get("execution_results") or [])),
        run_url,
        validation,
        error_message,
        description,
        validation_history=validation_history,
        fix_attempt=fix_attempt,
        max_fix_attempts=max_fix_attempts,
    )

    dry_run: bool = state.get("dry_run", True)

    if dry_run:
        logger.info(
            "[PR_CREATOR] dry_run=True — would create PR '%s' on %s (%s → %s) files=%s",
            pr_title, repo, fix_branch, pr_base,
            [f["path"] for f in files],
        )
        return {
            **state,
            "pr_url": None,
            "pr_body": pr_body,
            "final_summary": (
                f"[DRY RUN] Would create PR '{pr_title}' on {repo}\n"
                f"Branch: {fix_branch} → {pr_base}\n"
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
        fixed_snippet: str = file_entry.get("fixed_snippet", "") or ""
        explicit_content: str = file_entry.get("content", "") or ""

        if not path:
            logger.warning("[PR_CREATOR] Skipping file entry with no path")
            continue

        # When original_snippet is absent the LLM is providing a full-file replacement.
        # Two common patterns:
        #   1. LLM uses 'content' key only (full-file, no diff context)
        #   2. LLM uses 'fixed_snippet' with empty 'original_snippet' (full-file via snippet key)
        # _patch_file_content with empty original_snippet always returns None (safety guard),
        # so both cases must bypass it and use the content directly.
        if not original_snippet:
            content = fixed_snippet or explicit_content
            if not content:
                logger.warning("[PR_CREATOR] Skipping empty file entry: %s", path)
                continue
            logger.info("[PR_CREATOR] Using full-file replacement for %s (no original_snippet)", path)
        else:
            patch_source = fixed_snippet or explicit_content
            if not patch_source:
                logger.warning("[PR_CREATOR] Skipping empty file entry: %s", path)
                continue
            content = _patch_file_content(repo, path, branch, original_snippet, patch_source)
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
            "final_summary": "PR not created: all file commits failed:\n" + "\n".join(commit_errors),
        }

    pr_result = _adapter.run(
        "github", "create_pull_request",
        {
            "repo": repo,
            "title": pr_title,
            "body": pr_body,
            "branch": fix_branch,
            "base": pr_base,
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
    logger.info("[PR_CREATOR] PR created: %s (base=%s)", pr_url, pr_base)

    warn_block = ""
    if commit_errors:
        warn_block = "\nPartial commit errors:\n" + "\n".join(commit_errors)

    return {
        **state,
        "pr_url": pr_url,
        "fix_target_branch": pr_base,
        "fix_pr_number": context.get("pr_number") if event_kind == "github_pr_build_failure" else None,
        "final_summary": (
            f"PR created: {pr_url}\n"
            f"Root cause: {root_cause}\n"
            f"Files changed: {[f['path'] for f in files]}\n"
            f"Validation: {'passed' if validation.get('passed') else 'failed'}"
            f"{warn_block}"
        ),
    }


def _enrich_files_with_originals(
    files: list[dict[str, Any]],
    repo: str,
    branch: str,
    execution_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """For files that carry only 'content' (no original_snippet), find the
    original broken content so _build_pr_body can generate a real diff.

    Priority:
    1. execution_results — the actual broken file fetched during the CI run
       (most accurate "before" state, avoids diffing against a clean branch).
    2. Live GitHub fetch from branch (fallback for production when the branch
       still holds the broken file at PR-creation time).
    """
    def _find_in_execution_results(path: str) -> str:
        for step in (execution_results or []):
            if (
                step.get("action") == "get_file_content"
                and step.get("args", {}).get("path") == path
                and step.get("result", {}).get("status") == "ok"
            ):
                return step["result"].get("content", "")
        return ""

    enriched: list[dict[str, Any]] = []
    for f in files:
        if f.get("original_snippet") or not f.get("content"):
            enriched.append(f)
            continue
        path = f.get("path", "")
        original = _find_in_execution_results(path)
        if not original:
            orig_result = _adapter.run(
                "github", "get_file_content",
                {"repo": repo, "path": path, "ref": branch},
                dry_run=False,
            )
            if orig_result.get("status") == "ok":
                original = orig_result.get("content", "")
        if original:
            enriched.append({
                **f,
                "original_snippet": original,
                "fixed_snippet": f["content"],
            })
        else:
            enriched.append(f)
    return enriched


def _build_attempts_section(
    validation_history: list[dict[str, Any]],
    *,
    fix_attempt: int,
    max_fix_attempts: int,
) -> str:
    if not validation_history:
        return ""

    header = (
        f"### Repair Attempt History\n"
        f"Attempts used: {fix_attempt}/{max_fix_attempts}\n\n"
    )

    blocks: list[str] = []
    for index, attempt in enumerate(validation_history, start=1):
        status = "passed" if attempt.get("passed") else "failed"
        errors = "\n".join(attempt.get("errors", [])) or "(no errors)"
        output = (attempt.get("output", "") or "")[:1200]
        blocks.append(
            f"<details>\n"
            f"<summary>Attempt {index}: {status}</summary>\n\n"
            f"**Errors**\n"
            f"```\n{errors}\n```\n\n"
            f"**Output**\n"
            f"```\n{output}\n```\n"
            f"</details>\n\n"
        )

    return header + "".join(blocks)


def _build_pr_body(
    root_cause: str,
    files: list[dict[str, Any]],
    run_url: str,
    validation: dict[str, Any],
    error_message: str = "",
    description: str = "",
    validation_history: list[dict[str, Any]] | None = None,
    fix_attempt: int = 0,
    max_fix_attempts: int = 3,
) -> str:
    validation_status = "passed" if validation.get("passed") else "failed ❌"
    validation_output = validation.get("output", "")
    validation_errors = "\n".join(validation.get("errors", []))

    error_block = ""
    if error_message:
        error_block = f"**Error message:**\n```\n{error_message}\n```\n\n"

    desc_block = f"{description}\n\n" if description else ""

    file_list = "\n".join(f"- `{f['path']}`" for f in files)

    def _norm(s: str) -> str:
        return s.replace("\r\n", "\n").replace("\r", "\n")

    diff_sections = ""
    for f in files:
        path = f["path"]
        original = _norm(f.get("original_snippet", "") or "")
        fixed = _norm(f.get("fixed_snippet", "") or f.get("content", ""))
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

    attempts_section = _build_attempts_section(
        validation_history or [],
        fix_attempt=fix_attempt,
        max_fix_attempts=max_fix_attempts,
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
        f"{'> ⚠️ **Validation FAILED** — do NOT merge without manual review of the errors below.\n\n' if not validation.get('passed') else ''}"
        f"Status: {validation_status}\n\n"
        f"```\n{validation_output}\n{validation_errors}\n```\n\n"
        f"{attempts_section}"
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
