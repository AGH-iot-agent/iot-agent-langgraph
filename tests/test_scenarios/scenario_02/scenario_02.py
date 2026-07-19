from __future__ import annotations

import os
import time
from typing import Any, Optional

import pytest

from devops_agent.adapters.github_adapter import GHAdapter
from devops_agent.adapters.k8s_adapter import K8sAdapter
from devops_agent.event import AgentEvent
from devops_agent.graph import build_graph
from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.nodes.ci_fixer import ci_fixer_node
from devops_agent.nodes.sandbox_validator import sandbox_validator_node
from devops_agent.watchdog import AgentWatchdog

import os
import time
from typing import Any

import pytest
import yaml

from devops_agent.adapters.k8s_adapter import K8sAdapter
from devops_agent.nodes.ci_fixer import ci_fixer_node
from devops_agent.nodes.sandbox_validator import sandbox_validator_node
from tests.integration.test_ci_fix_integration import SMB_CHART_URI, _CIFixIntegrationAdapter, _assert_runtime_preconditions, _build_ci_failure_state, _require_openai_key, _uninstall_release_if_exists


SMB_CHART_URI    = os.environ.get("TEST_SMB_CHART_URI")
TARGET_REPO      = os.environ.get("TEST_CI_FIX_TARGET_REPO")
TARGET_NAMESPACE = os.environ.get("TEST_CI_FIX_TARGET_NAMESPACE")
TARGET_BRANCH    = os.environ.get("TEST_CI_FIX_TARGET_BRANCH")
KUBECONFIG       = os.environ.get("TEST_CI_FIX_KUBECONFIG")
TARGET_REPO      = os.environ.get("TEST_CI_FIX_TARGET_REPO")
TARGET_NAMESPACE = os.environ.get("TEST_CI_FIX_TARGET_NAMESPACE", "iotag-sbx")
BASE_BRANCH      = os.environ.get("TEST_CI_FIX_BASE_BRANCH", "main")
SMB_CHART_URI    = os.environ.get("TEST_SMB_CHART_URI")
KUBECONFIG       = os.environ.get("TEST_CI_FIX_KUBECONFIG")

_SBX_RELEASE_NAME  = "ci-fix-it-test"

_PROBE_ERROR_KEYWORDS = ("mapping values are not allowed", "livenessprobe")

CI_WAIT_TIMEOUT_S  = int(os.environ.get("TEST_CI_FIX_CI_WAIT_TIMEOUT_S", "1200"))
CI_POLL_INTERVAL_S = int(os.environ.get("TEST_CI_FIX_CI_POLL_INTERVAL_S", "20"))

BROKEN_DEV_VALUES_PATH = "./tests/test_scenarios/scenario_02/values-dev.yaml"
BROKEN_SBX_VALUES_PATH = "./tests/test_scenarios/scenario_02/values-sbx.yaml"

@pytest.fixture(scope="session")
def k8s_adapter() -> K8sAdapter:
    return K8sAdapter(kubeconfig=KUBECONFIG)

@pytest.fixture(scope="session")
def preflight_requirements(k8s_adapter: K8sAdapter) -> None:
    _assert_runtime_preconditions(k8s_adapter)

@pytest.fixture
def sbx_release(k8s_adapter: K8sAdapter) -> Any:
    _uninstall_release_if_exists(k8s_adapter, _SBX_RELEASE_NAME)
    try:
        yield _SBX_RELEASE_NAME
    finally:
        _uninstall_release_if_exists(k8s_adapter, _SBX_RELEASE_NAME)

@pytest.fixture(scope="session")
def k8s_adapter() -> K8sAdapter:
    return K8sAdapter(kubeconfig=KUBECONFIG)

@pytest.fixture(scope="session")
def broken_helm_values() -> str:
    """Fetch the intentionally broken Helm/values-sbx.yaml from GitHub.

    The branch ``test/scenario_02_1778954913`` is the permanent test-scenario
    branch that carries the unindented ``livenessProbe`` block.  Reading it
    live from GitHub ensures the fixture is authoritative and not duplicated
    in the local workspace.
    """
    from devops_agent.mcp_adapter import MCPAdapter

    _BROKEN_BRANCH = "test/scenario_02_1778954913"
    adapter = MCPAdapter()
    result = adapter.run("github", "get_file_content", {
        "repo": TARGET_REPO,
        "path": "Helm/values-sbx.yaml",
        "ref": _BROKEN_BRANCH,
    })
    if result.get("status") != "ok":
        pytest.fail(
            f"Failed to fetch Helm/values-sbx.yaml from GitHub "
            f"({TARGET_REPO}@{_BROKEN_BRANCH}): {result}"
        )
    return result["content"]


def _require_env() -> None:
    missing = [
        name for name, val in {
            "TEST_CI_FIX_TARGET_REPO": TARGET_REPO,
            "TEST_SMB_CHART_URI": SMB_CHART_URI,
        }.items() if not val
    ]
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


@pytest.fixture(scope="module")
def live_scenario02_pr(gh: GHAdapter) -> Any:
    """Open a real, broken PR - CI is expected to start running as soon as
    this fixture yields, since opening a PR against BASE_BRANCH triggers
    `on: pull_request` in the target repo's workflow."""
    _require_env()

    branch = f"test/scenario_02_true_e2e_{int(time.time())}"

    base_sha_result = gh.get_branch_sha(TARGET_REPO, BASE_BRANCH)
    assert base_sha_result.get("status") == "ok", (
        f"Could not resolve HEAD SHA for {TARGET_REPO}@{BASE_BRANCH}: {base_sha_result}"
    )
    base_sha = base_sha_result["sha"]

    branch_result = gh.create_branch(TARGET_REPO, branch, base_sha)
    assert branch_result.get("status") == "ok", (
        f"Failed to create branch {branch} on {TARGET_REPO}: {branch_result}"
    )

    head_sha: Optional[str] = None
    _BROKEN_VALUES_DEV = open(BROKEN_DEV_VALUES_PATH, "r", encoding="utf-8").read()
    _BROKEN_VALUES_SBX = open(BROKEN_SBX_VALUES_PATH, "r", encoding="utf-8").read()
    
    for path, content in (
        ("Helm/values-sbx.yaml", _BROKEN_VALUES_SBX),
        ("Helm/values-dev.yaml", _BROKEN_VALUES_DEV),
    ):
        commit_result = gh.commit_file(
            repo=TARGET_REPO,
            path=path,
            content=content,
            branch=branch,
            message=f"Update Helm values for scenario 02 ({path})",
        )
        assert commit_result.get("status") == "ok", (
            f"Failed to commit {path} on {branch}: {commit_result}"
        )
        head_sha = commit_result.get("sha") or head_sha

    if not head_sha:
        head_sha_result = gh.get_branch_sha(TARGET_REPO, branch)
        assert head_sha_result.get("status") == "ok", (
            f"Could not resolve HEAD SHA for {TARGET_REPO}@{branch} after commits: {head_sha_result}"
        )
        head_sha = head_sha_result["sha"]

    pr_result = gh.create_pull_request(
        repo=TARGET_REPO,
        title="[true-e2e-test] scenario 02: broken livenessProbe indentation",
        body=(
            "Automated TRUE e2e test PR (test_scenario02_full_e2e.py). "
            "This PR is expected to fail CI for real; the test waits for that "
            "failure, diagnoses it, validates a fix, and comments the reasoning "
            "back onto this PR. Safe to close/ignore if left open by a crashed run."
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
    either the workflow isn't triggering on this PR/branch, or the broken
    values file unexpectedly did not break CI.
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
                        f"conclusion={conclusion!r}, expected 'failure'. The broken "
                        f"values-sbx.yaml did not cause CI to fail as expected - check "
                        f"that the workflow actually validates Helm/values-sbx.yaml."
                    )
        time.sleep(CI_POLL_INTERVAL_S)

    pytest.fail(
        f"Timed out after {CI_WAIT_TIMEOUT_S}s waiting for a completed+failed CI run "
        f"on {repo}@{head_sha[:12]}. Runs observed for this commit: {last_seen_statuses or 'none'}. "
        f"Check that the target workflow triggers on 'pull_request', and increase "
        f"TEST_CI_FIX_CI_WAIT_TIMEOUT_S if the pipeline is just slow."
    )


@pytest.mark.integration
@pytest.mark.e2e
def test_scenario02_pr_fail_reasoning_validation_solution(
    live_scenario02_pr: dict[str, Any],
    k8s_adapter: K8sAdapter,
    mcp_adapter: MCPAdapter,
    gh: GHAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR -> FAIL -> REASONING -> VALIDATION -> SOLUTION, with every step
    backed by a real call against the live PR/CI/cluster - nothing faked."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.fail("OPENAI_API_KEY is required (ci_fixer calls the LLM).")

    branch = live_scenario02_pr["branch"]
    pr_number = live_scenario02_pr["pr_number"]
    head_sha = live_scenario02_pr["head_sha"]

    failing_run = _wait_for_real_ci_failure(gh, TARGET_REPO, head_sha)
    run_id = failing_run["id"]
    from devops_agent.nodes import ci_fixer as _ci_fixer_module
    from devops_agent.nodes import sandbox_validator as _sandbox_validator_module
    from devops_agent.nodes import pr_fix_commenter as _pr_fix_commenter_module
    from devops_agent.validators import helm_rules

    monkeypatch.setattr(_ci_fixer_module, "_adapter", mcp_adapter)
    monkeypatch.setattr(_sandbox_validator_module, "_adapter", mcp_adapter)
    monkeypatch.setattr(_pr_fix_commenter_module, "_adapter", mcp_adapter)
    monkeypatch.setattr(helm_rules, "_HELM_CHART_SOURCE", SMB_CHART_URI)

    watchdog = AgentWatchdog(adapter=mcp_adapter, graph=build_graph(), monitors=[])
    event = AgentEvent(
        kind="github_pr_build_failure",
        title=f"{TARGET_REPO}#{pr_number}: CI failed for branch {branch}",
        body=(
            "Live scenario_02 test event. CI run already failed; agent should complete full loop "
            "(reasoning + validation + PR comment) on this PR."
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
        live_scenario02_pr["posted_comment_ids"].append(newest_id)

def _fixed_proposal_state(
    k8s_adapter: K8sAdapter,
    broken_helm_values: str,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Run ci_fixer_node once and return the resulting state.

    Shared by several tests below so each doesn't have to re-derive it;
    kept as a plain helper (not a fixture) since a couple of tests need to
    patch different modules around the same call.
    """
    from devops_agent.nodes import ci_fixer as _ci_fixer_module

    stub_adapter = _CIFixIntegrationAdapter(k8s_adapter)
    monkeypatch.setattr(_ci_fixer_module, "_adapter", stub_adapter)
    state = _build_ci_failure_state(broken_helm_values)
    return ci_fixer_node(state)

# @pytest.mark.integration
# def test_ci_fixer_root_cause_identifies_probe_indentation(
#     preflight_requirements: None,
#     k8s_adapter: K8sAdapter,
#     broken_helm_values: str,
#     monkeypatch: pytest.MonkeyPatch,
# ) -> None:
#     _require_openai_key()
#     result_state = _fixed_proposal_state(k8s_adapter, broken_helm_values, monkeypatch)
#     root_cause = (result_state.get("ci_fix_proposal") or {}).get("root_cause", "")

#     assert root_cause, "Expected a non-empty root_cause."
#     root_cause_lower = root_cause.lower()
#     assert any(kw in root_cause_lower for kw in ("indent", "liveness", "probe")), (
#         f"root_cause does not mention indentation/probe - agent may have "
#         f"misdiagnosed the failure. root_cause={root_cause!r}"
#     )


# @pytest.mark.integration
# def test_ci_fixer_root_cause_does_not_misattribute_to_namespace(
#     preflight_requirements: None,
#     k8s_adapter: K8sAdapter,
#     broken_helm_values: str,
#     monkeypatch: pytest.MonkeyPatch,
# ) -> None:
#     _require_openai_key()
#     result_state = _fixed_proposal_state(k8s_adapter, broken_helm_values, monkeypatch)
#     root_cause = (result_state.get("ci_fix_proposal") or {}).get("root_cause", "").lower()

#     assert "namespace" not in root_cause, (
#         f"ci_fixer attributed scenario_02 (probe indentation) to a namespace "
#         f"issue - likely cross-contamination with the namespace-mismatch "
#         f"scenario. root_cause={root_cause!r}"
#     )


# @pytest.mark.integration
# def test_ci_fixer_proposed_content_is_valid_yaml(
#     preflight_requirements: None,
#     k8s_adapter: K8sAdapter,
#     broken_helm_values: str,
#     monkeypatch: pytest.MonkeyPatch,
# ) -> None:
#     _require_openai_key()
#     result_state = _fixed_proposal_state(k8s_adapter, broken_helm_values, monkeypatch)
#     proposal = result_state.get("ci_fix_proposal") or {}
#     files = {f["path"]: f["content"] for f in proposal.get("files", [])}

#     assert "Helm/values-sbx.yaml" in files, f"No fix proposed for values-sbx.yaml: {files.keys()}"
#     fixed_content = files["Helm/values-sbx.yaml"]
#     try:
#         yaml.safe_load(fixed_content)
#     except yaml.YAMLError as exc:
#         pytest.fail(
#             f"ci_fixer's proposed Helm/values-sbx.yaml is not valid YAML: {exc}\n"
#             f"--- content ---\n{fixed_content}"
#         )


# @pytest.mark.integration
# def test_ci_fixer_proposed_fix_actually_changes_the_broken_content(
#     preflight_requirements: None,
#     k8s_adapter: K8sAdapter,
#     broken_helm_values: str,
#     monkeypatch: pytest.MonkeyPatch,
# ) -> None:
#     _require_openai_key()
#     result_state = _fixed_proposal_state(k8s_adapter, broken_helm_values, monkeypatch)
#     proposal = result_state.get("ci_fix_proposal") or {}
#     files = {f["path"]: f["content"] for f in proposal.get("files", [])}
#     fixed_content = files.get("Helm/values-sbx.yaml", "")

#     assert fixed_content != broken_helm_values, (
#         "ci_fixer proposed the exact same content as the broken input - no fix was made."
#     )


# @pytest.mark.integration
# def test_ci_fixer_preserves_unrelated_values(
#     preflight_requirements: None,
#     k8s_adapter: K8sAdapter,
#     broken_helm_values: str,
#     monkeypatch: pytest.MonkeyPatch,
# ) -> None:
#     _require_openai_key()

#     result_state = _fixed_proposal_state(k8s_adapter, broken_helm_values, monkeypatch)
#     proposal = result_state.get("ci_fix_proposal") or {}
#     files = {f["path"]: f["content"] for f in proposal.get("files", [])}
#     fixed_content = files.get("Helm/values-sbx.yaml", "")

#     fixed_parsed = yaml.safe_load(fixed_content)
#     namespace = ((fixed_parsed or {}).get("global") or {}).get("namespace")
#     assert namespace == "iotag-sbx", (
#         f"Expected global.namespace to remain 'iotag-sbx' after the probe-indentation "
#         f"fix, got {namespace!r}. Fix may have touched unrelated config."
#     )

# @pytest.mark.integration
# def test_ci_fixer_scopes_fix_to_relevant_file_only(
#     preflight_requirements: None,
#     k8s_adapter: K8sAdapter,
#     broken_helm_values: str,
#     monkeypatch: pytest.MonkeyPatch,
# ) -> None:
#     _require_openai_key()
#     result_state = _fixed_proposal_state(k8s_adapter, broken_helm_values, monkeypatch)
#     proposal = result_state.get("ci_fix_proposal") or {}
#     file_paths = [f.get("path", "") for f in proposal.get("files", [])]

#     unexpected = [p for p in file_paths if p != "Helm/values-sbx.yaml"]
#     assert not unexpected, (
#         f"ci_fixer touched files beyond the one implicated in the failing logs: {unexpected}"
#     )

# @pytest.mark.integration
# def test_sandbox_validation_output_shows_helm_upgrade_actually_ran(
#     preflight_requirements: None,
#     k8s_adapter: K8sAdapter,
#     broken_helm_values: str,
#     monkeypatch: pytest.MonkeyPatch,
# ) -> None:
#     _require_openai_key()
#     from devops_agent.validators import helm_rules

#     monkeypatch.setattr(helm_rules, "_HELM_CHART_SOURCE", SMB_CHART_URI)
#     result_state = _fixed_proposal_state(k8s_adapter, broken_helm_values, monkeypatch)

#     release_name = f"ci-fix-evidence-{str(int(time.time()))[-8:]}"
#     state_for_validator = {
#         **result_state,
#         "context": {
#             **result_state.get("context", {}),
#             "release_name": release_name,
#             "chart_type": "deployment",
#         },
#     }
#     validated_state = sandbox_validator_node(state_for_validator)
#     validation = validated_state.get("validation_result") or {}

#     assert validation.get("passed") is True, f"Expected validation to pass, got: {validation}"
#     output = validation.get("output", "")
#     assert output, "validation passed=True but produced no output - can't confirm helm actually ran."


# @pytest.mark.integration
# def test_sandbox_validation_no_longer_shows_original_probe_error(
#     preflight_requirements: None,
#     k8s_adapter: K8sAdapter,
#     broken_helm_values: str,
#     monkeypatch: pytest.MonkeyPatch,
# ) -> None:
#     _require_openai_key()
#     from devops_agent.validators import helm_rules

#     monkeypatch.setattr(helm_rules, "_HELM_CHART_SOURCE", SMB_CHART_URI)
#     result_state = _fixed_proposal_state(k8s_adapter, broken_helm_values, monkeypatch)

#     release_name = f"ci-fix-noerr-{str(int(time.time()))[-8:]}"
#     state_for_validator = {
#         **result_state,
#         "context": {
#             **result_state.get("context", {}),
#             "release_name": release_name,
#             "chart_type": "deployment",
#         },
#     }
#     validated_state = sandbox_validator_node(state_for_validator)
#     validation = validated_state.get("validation_result") or {}
#     combined = " ".join(
#         [validation.get("output", "").lower()] + [e.lower() for e in validation.get("errors", [])]
#     )

#     for keyword in _PROBE_ERROR_KEYWORDS:
#         assert keyword not in combined, (
#             f"Original probe/indentation error text ('{keyword}') still present in "
#             f"validation output after the fix - the fix may not have actually resolved "
#             f"the YAML problem. output={combined[:500]!r}"
#         )


# @pytest.mark.integration
# def test_sandbox_validator_node_history_entry_has_expected_shape(
#     preflight_requirements: None,
#     k8s_adapter: K8sAdapter,
#     broken_helm_values: str,
#     monkeypatch: pytest.MonkeyPatch,
# ) -> None:
#     _require_openai_key()
#     from devops_agent.validators import helm_rules

#     monkeypatch.setattr(helm_rules, "_HELM_CHART_SOURCE", SMB_CHART_URI)
#     result_state = _fixed_proposal_state(k8s_adapter, broken_helm_values, monkeypatch)

#     release_name = f"ci-fix-history-{str(int(time.time()))[-8:]}"
#     state_for_validator = {
#         **result_state,
#         "context": {
#             **result_state.get("context", {}),
#             "release_name": release_name,
#             "chart_type": "deployment",
#         },
#     }
#     validated_state = sandbox_validator_node(state_for_validator)
#     history = validated_state.get("validation_history", [])

#     assert len(history) == 1, f"Expected exactly one validation_history entry, got {len(history)}"
#     entry = history[0]
#     assert "passed" in entry, f"validation_history entry missing 'passed' field: {entry}"
#     assert entry["passed"] is True
#     has_evidence = bool(entry.get("output") or entry.get("errors") or entry.get("message"))
#     assert has_evidence, (
#         f"validation_history entry has no output/errors/message to justify its 'passed' "
#         f"verdict: {entry}"
#     )


# @pytest.mark.integration
# def test_sandbox_validation_is_idempotent_across_repeated_runs(
#     preflight_requirements: None,
#     k8s_adapter: K8sAdapter,
#     broken_helm_values: str,
#     monkeypatch: pytest.MonkeyPatch,
# ) -> None:
#     _require_openai_key()
#     from devops_agent.validators import helm_rules

#     monkeypatch.setattr(helm_rules, "_HELM_CHART_SOURCE", SMB_CHART_URI)
#     result_state = _fixed_proposal_state(k8s_adapter, broken_helm_values, monkeypatch)

#     release_name = f"ci-fix-idempotent-{str(int(time.time()))[-8:]}"
#     base_context = {
#         **result_state.get("context", {}),
#         "release_name": release_name,
#         "chart_type": "deployment",
#     }

#     first_pass_state = sandbox_validator_node({**result_state, "context": base_context})
#     first_validation = first_pass_state.get("validation_result") or {}
#     assert first_validation.get("passed") is True, f"First validation run failed: {first_validation}"

#     second_pass_state = sandbox_validator_node({**result_state, "context": base_context})
#     second_validation = second_pass_state.get("validation_result") or {}
#     assert second_validation.get("passed") is True, (
#         f"Second validation run against the same release_name failed after the first "
#         f"succeeded: {second_validation}"
#     )

# @pytest.mark.integration
# def test_full_pipeline_root_cause_and_validation_are_consistent(
#     preflight_requirements: None,
#     k8s_adapter: K8sAdapter,
#     broken_helm_values: str,
#     monkeypatch: pytest.MonkeyPatch,
# ) -> None:
#     _require_openai_key()
#     # from devops_agent.validators import helm_rules

#     # monkeypatch.setattr(helm_rules, "_HELM_CHART_SOURCE", SMB_CHART_URI)
#     # result_state = _fixed_proposal_state(k8s_adapter, broken_helm_values, monkeypatch)
#     # proposal = result_state.get("ci_fix_proposal") or {}

#     # root_cause = proposal.get("root_cause", "")
#     # file_paths = [f.get("path", "") for f in proposal.get("files", [])]
#     # assert file_paths, f"No files in proposal to cross-check against root_cause={root_cause!r}"

#     # assert any(path.split("/")[-1] in root_cause for path in file_paths), (
#     #     f"root_cause does not reference the file(s) actually being fixed. "
#     #     f"root_cause={root_cause!r} file_paths={file_paths}"
#     # )

#     # release_name = f"ci-fix-consistency-{str(int(time.time()))[-8:]}"
#     # validated_state = sandbox_validator_node({
#     #     **result_state,
#     #     "context": {
#     #         **result_state.get("context", {}),
#     #         "release_name": release_name,
#     #         "chart_type": "deployment",
#     #     },
#     # })
#     # validation = validated_state.get("validation_result") or {}
#     # assert validation.get("passed") is True, (
#     #     f"Proposal consistent with root_cause, but failed sbx validation: {validation}"
#     # )