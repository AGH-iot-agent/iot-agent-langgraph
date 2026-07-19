from __future__ import annotations

import os
import re
import time
from typing import Any

import pytest

from devops_agent.adapters.github_adapter import GHAdapter
from devops_agent.adapters.k8s_adapter import K8sAdapter
from devops_agent.event import AgentEvent
from devops_agent.graph import build_graph
from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.watchdog import AgentWatchdog

TARGET_REPO      = os.environ.get("TEST_POM_FIX_TARGET_REPO", "AGH-iot-agent/iot-agent-gateway-api")
BASE_BRANCH      = os.environ.get("TEST_POM_FIX_BASE_BRANCH", "main")
KUBECONFIG       = os.environ.get("TEST_POM_FIX_KUBECONFIG")
TARGET_NAMESPACE = os.environ.get("TEST_POM_FIX_TARGET_NAMESPACE", "iotag-sbx")

POM_PATH = os.environ.get("TEST_POM_FIX_POM_PATH", "pom.xml")

DEPENDENCY_GROUP_ID     = os.environ.get("TEST_POM_FIX_DEP_GROUP_ID", "io.jsonwebtoken")
DEPENDENCY_ARTIFACT_ID  = os.environ.get("TEST_POM_FIX_DEP_ARTIFACT_ID", "jjwt")
DEPENDENCY_GOOD_VERSION = os.environ.get("TEST_POM_FIX_DEP_GOOD_VERSION", "0.9.1")
DEPENDENCY_BAD_VERSION  = os.environ.get("TEST_POM_FIX_DEP_BAD_VERSION", "99.99.99")

CI_WAIT_TIMEOUT_S  = int(os.environ.get("TEST_POM_FIX_CI_WAIT_TIMEOUT_S", "1200"))
CI_POLL_INTERVAL_S = int(os.environ.get("TEST_POM_FIX_CI_POLL_INTERVAL_S", "20"))


def _require_env() -> None:
    missing = [name for name, val in {"TEST_POM_FIX_TARGET_REPO": TARGET_REPO}.items() if not val]
    if missing:
        pytest.fail(f"Missing required environment variables: {missing}")


@pytest.fixture(scope="module")
def gh() -> GHAdapter:
    return GHAdapter()


@pytest.fixture(scope="module")
def k8s_adapter() -> K8sAdapter:
    return K8sAdapter(kubeconfig=KUBECONFIG)


@pytest.fixture(scope="module")
def mcp_adapter() -> MCPAdapter:
    return MCPAdapter()


def _bump_dependency_version(pom_xml: str, group_id: str, artifact_id: str, old_version: str, new_version: str) -> str:
    """Bump a single <dependency> version inside a pom.xml string.

    Deliberately a targeted regex rather than a full XML parser/rewriter:
    this mirrors what a real one-line developer diff would look like, and
    leaves the rest of the file byte-for-byte untouched.
    """
    pattern = re.compile(
        r"(<dependency>\s*"
        rf"<groupId>{re.escape(group_id)}</groupId>\s*"
        rf"<artifactId>{re.escape(artifact_id)}</artifactId>\s*"
        r"<version>)"
        rf"{re.escape(old_version)}"
        r"(</version>\s*</dependency>)",
        re.MULTILINE,
    )
    new_pom, count = pattern.subn(rf"\g<1>{new_version}\g<2>", pom_xml)
    if count != 1:
        pytest.fail(
            f"Expected exactly one <dependency> block for "
            f"{group_id}:{artifact_id}:{old_version} in {POM_PATH}, found {count}. "
            f"Check TEST_POM_FIX_DEP_* env vars against the real pom.xml layout."
        )
    return new_pom


@pytest.fixture(scope="module")
def base_pom_xml(mcp_adapter: MCPAdapter) -> str:
    """Fetch the current, working pom.xml from BASE_BRANCH."""
    _require_env()
    result = mcp_adapter.run("github", "get_file_content", {
        "repo": TARGET_REPO,
        "path": POM_PATH,
        "ref": BASE_BRANCH,
    })
    if result.get("status") != "ok":
        pytest.fail(f"Failed to fetch {POM_PATH} from {TARGET_REPO}@{BASE_BRANCH}: {result}")
    return result["content"]


@pytest.fixture
def live_gateway_pom_pr(gh: GHAdapter, base_pom_xml: str) -> Any:
    """Open a real, broken PR against iot-agent-gateway-api.

    Bumps `jjwt` to a version that has never existed on Maven Central, commits
    it, and opens a PR against BASE_BRANCH. CI is expected to start running
    as soon as this fixture yields, since opening a PR against BASE_BRANCH
    triggers `on: pull_request` in the target repo's workflow.
    """
    _require_env()

    branch = f"test/scenario04_gateway_pom_e2e_{int(time.time())}"

    base_sha_result = gh.get_branch_sha(TARGET_REPO, BASE_BRANCH)
    assert base_sha_result.get("status") == "ok", (
        f"Could not resolve HEAD SHA for {TARGET_REPO}@{BASE_BRANCH}: {base_sha_result}"
    )
    base_sha = base_sha_result["sha"]

    branch_result = gh.create_branch(TARGET_REPO, branch, base_sha)
    assert branch_result.get("status") == "ok", (
        f"Failed to create branch {branch} on {TARGET_REPO}: {branch_result}"
    )

    broken_pom = _bump_dependency_version(
        base_pom_xml,
        DEPENDENCY_GROUP_ID,
        DEPENDENCY_ARTIFACT_ID,
        DEPENDENCY_GOOD_VERSION,
        DEPENDENCY_BAD_VERSION,
    )

    commit_result = gh.commit_file(
        repo=TARGET_REPO,
        path=POM_PATH,
        content=broken_pom,
        branch=branch,
        message=(
            f"Bump {DEPENDENCY_ARTIFACT_ID} to {DEPENDENCY_BAD_VERSION} "
            f"(unpublished - scenario 04 e2e test)"
        ),
    )
    assert commit_result.get("status") == "ok", f"Failed to commit {POM_PATH} on {branch}: {commit_result}"
    head_sha = commit_result.get("sha")
    if not head_sha:
        head_sha_result = gh.get_branch_sha(TARGET_REPO, branch)
        assert head_sha_result.get("status") == "ok", (
            f"Could not resolve HEAD SHA for {TARGET_REPO}@{branch} after commit: {head_sha_result}"
        )
        head_sha = head_sha_result["sha"]

    pr_result = gh.create_pull_request(
        repo=TARGET_REPO,
        title=f"[true-e2e-test] scenario 04: bump {DEPENDENCY_ARTIFACT_ID} to unpublished {DEPENDENCY_BAD_VERSION}",
        body=(
            "Automated TRUE e2e test PR (test_scenario04_gateway_pom_e2e.py). "
            f"This PR bumps `{DEPENDENCY_ARTIFACT_ID}` from `{DEPENDENCY_GOOD_VERSION}` to "
            f"`{DEPENDENCY_BAD_VERSION}`, a version that has never been published to the "
            "package registry. It is expected to fail CI for real - first at Maven "
            "dependency resolution, then at the `docker build` step, since the image build "
            "tries to package a jar that was never produced. The test waits for that "
            "failure, dispatches the agent, and checks that it traces the docker-build "
            "symptom back to the real Maven root cause. Safe to close/ignore if left open "
            "by a crashed run."
        ),
        branch=branch,
        base=BASE_BRANCH,
        dry_run=False,
    )
    assert pr_result.get("status") == "ok", f"Failed to open PR for {branch}: {pr_result}"
    pr_url = pr_result["pr_url"]
    pr_number = int(pr_url.rstrip("/").rsplit("/", 1)[-1])

    info: dict[str, Any] = {
        "branch": branch,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "head_sha": head_sha,
        "posted_comment_ids": [],
    }

    try:
        yield info
    finally:
        gh.close_pull_request(TARGET_REPO, pr_number)
        gh.delete_branch(TARGET_REPO, branch)


def _wait_for_real_ci_failure(gh: GHAdapter, repo: str, head_sha: str) -> dict[str, Any]:
    """Block until GitHub Actions reports a completed, failed run for
    ``head_sha`` - a genuine wait on the live system, no shortcuts.

    Returns the failing run dict. Fails the test (with a clear message) if
    CI does not report a failure within CI_WAIT_TIMEOUT_S, since that means
    either the workflow isn't triggering on this PR/branch, or the version
    bump unexpectedly did not break the build.
    """
    deadline = time.time() + CI_WAIT_TIMEOUT_S
    last_seen_statuses: dict[int, str] = {}

    while time.time() < deadline:
        runs_resp = gh.get_workflow_runs(repo, per_page=30)
        if runs_resp.get("status") == "ok":
            for run in runs_resp.get("runs", []):
                if run.get("head_sha") != head_sha:
                    continue
                run_id = run.get("id")
                status = run.get("status")
                conclusion = run.get("conclusion")
                last_seen_statuses[run_id] = f"{status}/{conclusion}"

                if status == "completed":
                    if conclusion == "failure":
                        return run
                    pytest.fail(
                        f"CI run #{run_id} for {head_sha[:12]} completed with "
                        f"conclusion={conclusion!r}, expected 'failure'. The unpublished "
                        f"dependency bump did not cause CI to fail as expected - check that "
                        f"the workflow actually builds/resolves {POM_PATH}."
                    )
        time.sleep(CI_POLL_INTERVAL_S)

    pytest.fail(
        f"Timed out after {CI_WAIT_TIMEOUT_S}s waiting for a completed+failed CI run "
        f"on {repo}@{head_sha[:12]}. Runs observed for this commit: {last_seen_statuses or 'none'}. "
        f"Check that the target workflow triggers on 'pull_request', and increase "
        f"TEST_POM_FIX_CI_WAIT_TIMEOUT_S if the pipeline is just slow."
    )


@pytest.mark.integration
@pytest.mark.e2e
def test_scenario04_gateway_pom_bump_docker_build_root_cause(
    live_gateway_pom_pr: dict[str, Any],
    k8s_adapter: K8sAdapter,
    mcp_adapter: MCPAdapter,
    gh: GHAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR -> FAIL -> REASONING -> PR COMMENT, with every step backed by a
    real call against the live PR/CI - nothing faked.

    The specific thing under test: CI fails visibly at the `docker build`
    step (because the image build packages a jar that was never produced),
    and the agent must trace that failure back to its true root cause - the
    Maven dependency-resolution error for the unpublished
    `jjwt:99.99.99` artifact - rather than stopping at "docker
    build failed".
    """
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.fail("OPENAI_API_KEY is required (ci_fixer calls the LLM).")

    branch = live_gateway_pom_pr["branch"]
    pr_number = live_gateway_pom_pr["pr_number"]
    head_sha = live_gateway_pom_pr["head_sha"]

    failing_run = _wait_for_real_ci_failure(gh, TARGET_REPO, head_sha)
    run_id = failing_run["id"]

    from devops_agent.nodes import ci_fixer as _ci_fixer_module
    from devops_agent.nodes import sandbox_validator as _sandbox_validator_module
    from devops_agent.nodes import pr_fix_commenter as _pr_fix_commenter_module

    monkeypatch.setattr(_ci_fixer_module, "_adapter", mcp_adapter)
    monkeypatch.setattr(_sandbox_validator_module, "_adapter", mcp_adapter)
    monkeypatch.setattr(_pr_fix_commenter_module, "_adapter", mcp_adapter)

    watchdog = AgentWatchdog(adapter=mcp_adapter, graph=build_graph(), monitors=[])
    event = AgentEvent(
        kind="github_pr_build_failure",
        title=f"{TARGET_REPO}#{pr_number}: CI failed for branch {branch}",
        body=(
            "Live scenario_04 test event. CI run already failed at the docker build step; "
            "agent should trace this back to the underlying Maven dependency resolution "
            "failure and complete the full loop (reasoning + PR comment) on this PR."
        ),
        context={
            "repo_full_name": TARGET_REPO,
            "branch": branch,
            "pr_number": pr_number,
            "run_id": run_id,
            "run_url": failing_run.get("html_url", ""),
            "namespace": TARGET_NAMESPACE,
        },
    )
    watchdog._dispatch_inner(event)

    comments_result = gh.get_pr_comments(TARGET_REPO, pr_number)
    assert comments_result.get("status") == "ok", comments_result
    comments = comments_result.get("comments", [])
    matching = [c for c in comments if "## gh_action_bot" in c.get("body", "")]
    assert matching, (
        f"Expected a '## gh_action_bot' comment on PR#{pr_number} after full agent loop "
        f"for github_pr_build_failure, got bodies: {[c.get('body', '')[:120] for c in comments]}"
    )

    newest_id = matching[-1].get("id")
    if newest_id:
        live_gateway_pom_pr["posted_comment_ids"].append(newest_id)

    comment_body = matching[-1].get("body", "").lower()

    assert DEPENDENCY_ARTIFACT_ID.lower() in comment_body, (
        f"Agent's PR comment does not mention the offending dependency "
        f"'{DEPENDENCY_ARTIFACT_ID}' - it may have stopped at the docker-build "
        f"symptom instead of tracing back to the Maven root cause. "
        f"comment={comment_body[:500]!r}"
    )
    assert DEPENDENCY_BAD_VERSION in comment_body, (
        f"Agent's PR comment does not mention the unpublished version "
        f"'{DEPENDENCY_BAD_VERSION}' - root-cause identification may be incomplete. "
        f"comment={comment_body[:500]!r}"
    )

    assert POM_PATH in comment_body, (
        f"Agent's PR comment does not propose a change to '{POM_PATH}' - it may have "
        f"misclassified this as a Helm/manifest issue instead of a Maven dependency "
        f"issue. comment={comment_body[:500]!r}"
    )
    _helm_paths_mentioned = [
        p for p in ("helm/values-dev.yaml", "helm/values-sbx.yaml") if p in comment_body
    ]
    assert not _helm_paths_mentioned, (
        f"Agent's PR comment proposes changes to Helm values file(s) {_helm_paths_mentioned} "
        f"for what is a pure Maven dependency-resolution failure - this is the exact "
        f"misclassification regression this test guards against. "
        f"comment={comment_body[:800]!r}"
    )