"""Integration tests for the CI-fix pipeline (scenario_02: broken probe indentation).

Scenario: A PR on iot-agent-login-screen fails CI because Helm/values-sbx.yaml
has a ``livenessProbe`` block whose child keys have no indentation - taken
directly from iot-agent-tests/scenario_02/values-sbx.yaml.

These tests verify:
  1. The broken values FAIL sbx sandbox validation  (regression guard).
  2. ``ci_fixer_node`` identifies the root cause and proposes a fix for
     Helm/values-sbx.yaml.
  3. The ci_fixer-proposed fix PASSES sbx sandbox validation against the real
     SMB Helm chart.
  4. ``sandbox_validator_node`` (the full node, not just the function) passes
     for the ci_fixer proposal, and no GitHub write calls are made.
  5. ``pr_creator_node`` in dry_run=True does NOT post anything to GitHub, but
     produces a human-readable final_summary.
  6. Unit-level routing checks for ``route_after_validation``.

All @pytest.mark.integration tests require:
  - A reachable Kubernetes cluster (``kubectl get pods`` must work).
  - The SMB share accessible at TEST_SMB_CHART_URI.
  - Tests 2–5 also require OPENAI_API_KEY (ci_fixer calls the LLM).
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

import json as _json
import pytest

from devops_agent.adapters.k8s_adapter import K8sAdapter
from devops_agent.nodes.ci_fixer import ci_fixer_node
from devops_agent.nodes.pr_creator import pr_creator_node
from devops_agent.nodes.sandbox_validator import route_after_validation, sandbox_validator_node
from devops_agent.validators.orchestrator import validate_fix_proposal

SMB_CHART_URI    = os.environ.get("TEST_SMB_CHART_URI")
TARGET_REPO      = os.environ.get("TEST_CI_FIX_TARGET_REPO")
TARGET_NAMESPACE = os.environ.get("TEST_CI_FIX_TARGET_NAMESPACE")
TARGET_BRANCH    = os.environ.get("TEST_CI_FIX_TARGET_BRANCH")
KUBECONFIG       = os.environ.get("TEST_CI_FIX_KUBECONFIG")

_FAKE_RUN_ID       = os.environ.get("TEST_CI_FAKE_RUN_ID", "9876543")
_FAKE_RUN_URL      = f"https://github.com/{TARGET_REPO}/actions/runs/{_FAKE_RUN_ID}"
_SBX_RELEASE_NAME  = "ci-fix-it-test"
_E2E_RELEASE_NAME  = "live-e2e-test"
_LIVE_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pr71_execution_results.json"

_REAL_RUN_ID  = 25969747491
_PR_NUMBER    = 71
_REAL_RUN_URL = f"https://github.com/{TARGET_REPO}/actions/runs/{_REAL_RUN_ID}"

_GITHUB_WRITE_TOOLS = frozenset({
    "create_pull_request",
    "create_branch",
    "commit_file",
    "create_issue_comment",
})

_FAKE_CI_JOB_LOGS = (
    "##[group]Run helm upgrade --dry-run\n"
    "Error: INSTALLATION FAILED: YAML parse error on Helm/values-sbx.yaml: "
    "error converting YAML to JSON: yaml: line 42: "
    "mapping values are not allowed here (livenessProbe key)\n"
    "helm.go:84: [debug] error upgrading release 'iot-agent-login-screen'\n"
    "##[error]Process completed with exit code 1.\n"
)

class _CIFixIntegrationAdapter:
    """Adapter used by scenario_02 integration tests.

    - GitHub *reads*  - real MCPAdapter (live GitHub API, so fetch_base_text
      gets the actual values-sbx.yaml from the target branch for patch validation)
    - GitHub *writes* - intercepted: recorded in ``github_write_calls`` but
      never forwarded, ensuring no state is mutated on GitHub.
    - Kubernetes      - real K8sAdapter (live cluster for helm --dry-run).
    """

    def __init__(self, k8s: K8sAdapter) -> None:
        from devops_agent.mcp_adapter import MCPAdapter as _RealMCPAdapter
        self._k8s = k8s
        self._real = _RealMCPAdapter()
        self.github_write_calls: list[dict[str, Any]] = []

    def run(self, service: str, tool: str, params: dict, dry_run: bool = False) -> dict:
        if service == "github":
            if tool in _GITHUB_WRITE_TOOLS:
                self.github_write_calls.append({"tool": tool, "params": params})
                return _fake_github_write_response(tool)
            return self._real.run(service, tool, params, dry_run=dry_run)
        if service == "kubernetes":
            return self._k8s.run(tool, params, dry_run=dry_run)
        return {"status": "error", "message": f"Unsupported service in test adapter: {service}/{tool}"}

def _fake_github_write_response(tool: str) -> dict:
    match tool:
        case "create_issue_comment":
            return {"status": "ok", "pr_url": "https://github.com/test/pr/999", "number": 999}
        case "create_branch":
            return {"status": "ok", "sha": "abc123def456"}
        case "commit_file":
            return {"status": "ok", "sha": "def456abc789"}
        case _:
            return {"status": "ok"}

def _load_live_execution_results() -> list[dict]:
    """Load the pre-captured executor snapshot for PR #71.

    Run ``tests/integration/generate_live_fixture.py`` once to regenerate
    this file when the target PR or CI run changes.
    """
    if not _LIVE_FIXTURE_PATH.exists():
        pytest.fail(
            f"Live executor snapshot not found: {_LIVE_FIXTURE_PATH}. "
            "Run tests/integration/generate_live_fixture.py once to generate it."
        )
    return _json.loads(_LIVE_FIXTURE_PATH.read_text(encoding="utf-8"))

def _require_openai_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.fail(
            "OPENAI_API_KEY is required for LLM-driven ci_fixer integration tests. "
            "Set the variable and re-run."
        )

def _assert_runtime_preconditions(k8s: K8sAdapter) -> None:
    for binary in ("helm", "kubectl"):
        if not shutil.which(binary):
            pytest.fail(f"Required binary not found in PATH: {binary}")

    check = k8s.run("get_rollout_status", {"namespace": TARGET_NAMESPACE}, dry_run=False)
    if check.get("status") != "ok":
        pytest.fail(
            f"Kubernetes cluster preflight failed for namespace {TARGET_NAMESPACE}: "
            f"{check.get('message', '')}"
        )

    try:
        downloaded, tmp_dir = k8s._download_smb_chart(SMB_CHART_URI)
    except Exception as exc:
        pytest.fail(
            f"SMB chart preflight failed ({SMB_CHART_URI}). "
            f"Set SMB_USERNAME/SMB_PASSWORD if auth is required. Details: {exc}"
        )
    try:
        if not Path(downloaded).exists() or Path(downloaded).stat().st_size == 0:
            pytest.fail("Downloaded SMB chart is missing or empty.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _build_execution_results(broken_values: str) -> list[dict[str, Any]]:
    """Simulate the execution_results that executor_node would collect for a
    failing CI run on scenario_02 (broken livenessProbe indentation)."""
    return [
        {
            "step": 1,
            "tool": "github",
            "action": "get_job_logs",
            "args": {"repo": TARGET_REPO, "run_id": _FAKE_RUN_ID, "job_id": 0},
            "result": {"status": "ok", "logs": _FAKE_CI_JOB_LOGS},
        },
        {
            "step": 2,
            "tool": "github",
            "action": "get_file_content",
            "args": {"repo": TARGET_REPO, "path": "Helm/values-sbx.yaml", "ref": TARGET_BRANCH},
            "result": {"status": "ok", "content": broken_values},
        },
        {
            "step": 3,
            "tool": "github",
            "action": "get_file_content",
            "args": {"repo": TARGET_REPO, "path": ".github/workflows/ci.yml", "ref": TARGET_BRANCH},
            "result": {"status": "ok", "content": "# ci workflow placeholder\n"},
        },
        {
            "step": 4,
            "tool": "kubernetes",
            "action": "get_pods",
            "args": {"namespace": TARGET_NAMESPACE},
            "result": {"status": "ok", "pods": []},
        },
    ]


def _uninstall_release_if_exists(
    k8s: K8sAdapter, release_name: str, namespace: str = TARGET_NAMESPACE
) -> None:
    """Best-effort ``helm uninstall`` for ``release_name`` in ``namespace``.

    Used both before a test (to clear leftover state from a previous run
    that crashed or was interrupted before teardown ran) and after a test
    (routine cleanup).
    """
    result = k8s.run(
        "helm_uninstall",
        {"release_name": release_name, "namespace": namespace},
        dry_run=False,
    )
    status = result.get("status")
    if status == "ok":
        return
    message = str(result.get("message", "")).lower()
    if "not found" in message or "release: not found" in message:
        return
    pytest.fail(
        f"Failed to clean up Helm release '{release_name}' in namespace "
        f"'{namespace}' before/after test: {result}"
    )

def _build_ci_failure_state(broken_values: str) -> dict[str, Any]:
    return {
        "request": f"Fix CI failure on {TARGET_REPO} branch {TARGET_BRANCH}",
        "dry_run": True,
        "event_kind": "github_ci_failure",
        "context": {
            "repo_full_name": TARGET_REPO,
            "branch": TARGET_BRANCH,
            "run_id": _FAKE_RUN_ID,
            "run_url": _FAKE_RUN_URL,
            "namespace": TARGET_NAMESPACE,
        },
        "execution_results": _build_execution_results(broken_values),
        "fix_attempt": 0,
        "max_fix_attempts": 3,
        "validation_history": [],
    }

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
def preflight_requirements(k8s_adapter: K8sAdapter) -> None:
    _assert_runtime_preconditions(k8s_adapter)

@pytest.fixture
def sbx_release(k8s_adapter: K8sAdapter) -> Any:
    """Yields the fixed sbx release name, guaranteeing a clean slate."""
    _uninstall_release_if_exists(k8s_adapter, _SBX_RELEASE_NAME)
    try:
        yield _SBX_RELEASE_NAME
    finally:
        _uninstall_release_if_exists(k8s_adapter, _SBX_RELEASE_NAME)

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

@pytest.mark.integration
def test_broken_scenario02_values_fail_sbx_validation(
    preflight_requirements: None,
    k8s_adapter: K8sAdapter,
    broken_helm_values: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: the unmodified scenario_02 values-sbx.yaml must FAIL
    sbx sandbox validation. """
    from devops_agent.validators import helm_rules

    monkeypatch.setattr(helm_rules, "_HELM_CHART_SOURCE", SMB_CHART_URI)

    adapter = _CIFixIntegrationAdapter(k8s_adapter)
    proposal = {
        "files": [{"path": "Helm/values-sbx.yaml", "content": broken_helm_values}],
        "root_cause": "broken_helm_values_regression_guard",
    }
    context = {
        "namespace": TARGET_NAMESPACE,
        "repo_full_name": TARGET_REPO,
        "branch": TARGET_BRANCH,
        "release_name": f"ci-fix-broken-{str(int(time.time()))[-8:]}",
        "chart_type": "deployment",
    }

    result = validate_fix_proposal(adapter, proposal=proposal, context=context)

    assert result.get("passed") is False, (
        "Broken scenario_02 values should FAIL validation but passed. "
        "Check that iot-agent-tests/scenario_02/values-sbx.yaml still has the "
        f"unindented livenessProbe block. result={result}"
    )
    errors_str = " ".join(result.get("errors", []) + [result.get("output", "")])
    assert any(
        kw in errors_str.lower()
        for kw in ("error", "fail", "invalid", "unmarshal", "parse", "key", "probe")
    ), f"Expected a helm/yaml error keyword in validation output, got: {result}"

    assert adapter.github_write_calls == [], (
        f"Validation must not make GitHub write calls, got: {adapter.github_write_calls}"
    )

@pytest.mark.integration
def test_ci_fixer_detects_probe_indentation_root_cause(
    preflight_requirements: None,
    k8s_adapter: K8sAdapter,
    broken_helm_values: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ci_fixer_node must identify the livenessProbe indentation root cause
    and propose a change to Helm/values-sbx.yaml.  Requires OPENAI_API_KEY."""
    _require_openai_key()

    from devops_agent.nodes import ci_fixer as _ci_fixer_module

    stub_adapter = _CIFixIntegrationAdapter(k8s_adapter)
    monkeypatch.setattr(_ci_fixer_module, "_adapter", stub_adapter)
    state = _build_ci_failure_state(broken_helm_values)
    result_state = ci_fixer_node(state)
    proposal = result_state.get("ci_fix_proposal") or {}
    
    assert proposal.get("files"), f"Expected at least one file in proposal, got: {proposal}"

    file_paths = [f.get("path", "") for f in proposal["files"]]
    assert "Helm/values-sbx.yaml" in file_paths, (
        f"Expected ci_fixer to propose Helm/values-sbx.yaml, got: {file_paths}"
    )
    assert stub_adapter.github_write_calls == [], (
        f"ci_fixer_node must not make GitHub write calls, but made: {stub_adapter.github_write_calls}"
    )

@pytest.mark.integration
def test_ci_fixer_proposal_passes_sbx_validation_with_smb_chart(
    preflight_requirements: None,
    k8s_adapter: K8sAdapter,
    broken_helm_values: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full pipeline: ci_fixer proposes a fix for scenario_02 -> sbx sandbox
    validator confirms it is deployable against the real SMB Helm chart.
    No GitHub writes occur at any stage.  Requires OPENAI_API_KEY."""
    _require_openai_key()

    from devops_agent.nodes import ci_fixer as _ci_fixer_module
    from devops_agent.validators import helm_rules

    stub_adapter = _CIFixIntegrationAdapter(k8s_adapter)
    monkeypatch.setattr(_ci_fixer_module, "_adapter", stub_adapter)
    monkeypatch.setattr(helm_rules, "_HELM_CHART_SOURCE", SMB_CHART_URI)

    state = _build_ci_failure_state(broken_helm_values)
    state_after_fix = ci_fixer_node(state)

    proposal = state_after_fix.get("ci_fix_proposal") or {}
    assert proposal.get("files"), f"ci_fixer produced no file entries. proposal={proposal}"

    validation_adapter = _CIFixIntegrationAdapter(k8s_adapter)
    context = {
        "namespace": TARGET_NAMESPACE,
        "repo_full_name": TARGET_REPO,
        "branch": TARGET_BRANCH,
        "release_name": f"ci-fix-it-{str(int(time.time()))[-8:]}",
        "chart_type": "deployment",
    }

    validation_result = validate_fix_proposal(validation_adapter, proposal=proposal, context=context)

    assert validation_result.get("passed") is True, (
        "ci_fixer proposal did not pass sbx validation. "
        f"errors={validation_result.get('errors')} "
        f"output={validation_result.get('output', '')[:800]}"
    )
    assert validation_adapter.github_write_calls == [], (
        f"Validation must not make GitHub write calls, got: {validation_adapter.github_write_calls}"
    )

@pytest.mark.integration
def test_sandbox_validator_node_passes_ci_fixer_proposal(
    preflight_requirements: None,
    k8s_adapter: K8sAdapter,
    broken_helm_values: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end node-level test: ci_fixer_node → sandbox_validator_node.
    Uses the real K8s cluster and the SMB Helm chart.  Checks that
    validation_history is updated and no GitHub writes are made.
    Requires OPENAI_API_KEY."""
    _require_openai_key()

    from devops_agent.nodes import ci_fixer as _ci_fixer_module
    from devops_agent.nodes import sandbox_validator as _sandbox_validator_module
    from devops_agent.validators import helm_rules

    stub_adapter = _CIFixIntegrationAdapter(k8s_adapter)
    monkeypatch.setattr(_ci_fixer_module, "_adapter", stub_adapter)
    monkeypatch.setattr(_sandbox_validator_module, "_adapter", stub_adapter)
    monkeypatch.setattr(helm_rules, "_HELM_CHART_SOURCE", SMB_CHART_URI)

    state = _build_ci_failure_state(broken_helm_values)
    state_after_fix = ci_fixer_node(state)
    assert state_after_fix.get("ci_fix_proposal", {}).get("files"), (
        f"ci_fixer produced no files. proposal={state_after_fix.get('ci_fix_proposal')}"
    )

    state_for_validator = {
        **state_after_fix,
        "context": {
            **state_after_fix.get("context", {}),
            "release_name": f"sbx-node-it-{str(int(time.time()))[-8:]}",
            "chart_type": "deployment",
        },
    }
    result_state = sandbox_validator_node(state_for_validator)

    validation_result = result_state.get("validation_result") or {}
    assert validation_result.get("passed") is True, (
        "sandbox_validator_node did not pass for ci_fixer proposal. "
        f"errors={validation_result.get('errors')} "
        f"output={validation_result.get('output', '')[:800]}"
    )
    assert len(result_state.get("validation_history", [])) == 1, (
        "Expected exactly one entry appended to validation_history."
    )
    assert stub_adapter.github_write_calls == [], (
        f"sandbox_validator must not make GitHub write calls, got: {stub_adapter.github_write_calls}"
    )

@pytest.mark.integration
def test_pr_creator_dry_run_does_not_post_to_github(
    preflight_requirements: None,
    k8s_adapter: K8sAdapter,
    broken_helm_values: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pr_creator_node with dry_run=True must produce a final_summary describing
    the would-be PR but must NOT call any GitHub write API.
    Requires OPENAI_API_KEY (ci_fixer is part of the pipeline)."""
    _require_openai_key()

    from devops_agent.nodes import ci_fixer as _ci_fixer_module
    from devops_agent.nodes import pr_creator as _pr_creator_module
    from devops_agent.nodes import sandbox_validator as _sandbox_validator_module
    from devops_agent.validators import helm_rules

    stub_adapter = _CIFixIntegrationAdapter(k8s_adapter)
    monkeypatch.setattr(_ci_fixer_module, "_adapter", stub_adapter)
    monkeypatch.setattr(_pr_creator_module, "_adapter", stub_adapter)
    monkeypatch.setattr(_sandbox_validator_module, "_adapter", stub_adapter)
    monkeypatch.setattr(helm_rules, "_HELM_CHART_SOURCE", SMB_CHART_URI)

    state = _build_ci_failure_state(broken_helm_values)
    state_after_fix = ci_fixer_node(state)
    assert state_after_fix.get("ci_fix_proposal", {}).get("files"), (
        f"ci_fixer produced no files. proposal={state_after_fix.get('ci_fix_proposal')}"
    )

    state_for_validator = {
        **state_after_fix,
        "context": {
            **state_after_fix.get("context", {}),
            "release_name": f"pr-dry-run-{str(int(time.time()))[-8:]}",
            "chart_type": "deployment",
        },
    }
    state_after_validation = sandbox_validator_node(state_for_validator)
    assert state_after_validation.get("validation_result", {}).get("passed") is True, (
        "sandbox_validator did not pass - pr_creator test cannot proceed. "
        f"errors={state_after_validation.get('validation_result', {}).get('errors')}"
    )

    state_for_pr = {
        **state_after_validation,
        "dry_run": True,
    }
    result_state = pr_creator_node(state_for_pr)

    final_summary = result_state.get("final_summary", "")
    assert "[DRY RUN]" in final_summary, (
        f"Expected '[DRY RUN]' tag in final_summary, got: {final_summary!r}"
    )
    assert result_state.get("pr_url") is None, (
        "pr_url should be None in dry_run mode - no real PR should be created."
    )
    assert stub_adapter.github_write_calls == [], (
        f"pr_creator_node must not make GitHub write calls in dry_run mode, "
        f"but made: {stub_adapter.github_write_calls}"
    )

    pr_body = result_state.get("pr_body", "")
    assert pr_body, "Expected pr_body to be present in dry_run result state."
    _LOGS_DIR = Path(__file__).resolve().parents[3] / "logs" / "test" / "pr_results"
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _FIXTURES_DIR = Path(__file__).parent / "fixtures"
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    fixture_path = _FIXTURES_DIR / "last_dry_run_pr_body.md"
    fixture_path.write_text(pr_body, encoding="utf-8")
    (_LOGS_DIR / "last_dry_run_pr_body.md").write_text(pr_body, encoding="utf-8")

    for file_entry in result_state.get("ci_fix_proposal", {}).get("files", []):
        fname = Path(file_entry.get("path", "")).name
        content = file_entry.get("content", "")
        if fname and content:
            (_FIXTURES_DIR / fname).write_text(content, encoding="utf-8")
            (_LOGS_DIR / fname).write_text(content, encoding="utf-8")

    _vr = state_after_validation.get("validation_result") or {}
    _dry_run_output = "\n".join(filter(None, [
        _vr.get("output", ""),
        *_vr.get("errors", []),
    ]))
    (_FIXTURES_DIR / "dry_run_output.txt").write_text(_dry_run_output, encoding="utf-8")
    (_LOGS_DIR / "dry_run_output.txt").write_text(_dry_run_output, encoding="utf-8")

def test_route_after_validation_routes_to_pr_creator_on_success() -> None:
    state = {
        "validation_result": {"passed": True, "errors": []},
        "fix_attempt": 1,
        "max_fix_attempts": 3,
        "event_kind": "github_ci_failure",
        "ci_fix_proposal": {"files": [{"path": "Helm/values-sbx.yaml", "content": "x: 1"}]},
    }
    assert route_after_validation(state) == "pr_creator"


def test_route_after_validation_pr_build_failure_still_comments() -> None:
    """github_pr_build_failure keeps pr_fix_commenter so the original PR gets the
    thesis comment; that node opens the fix PR after validation passes."""
    state = {
        "validation_result": {"passed": True, "errors": []},
        "fix_attempt": 1,
        "max_fix_attempts": 5,
        "event_kind": "github_pr_build_failure",
        "ci_fix_proposal": {"files": [{"path": "Helm/values-sbx.yaml", "content": "replicaCount: 1\n"}]},
    }
    assert route_after_validation(state) == "pr_fix_commenter"

def test_route_after_validation_retries_ci_fixer_on_failure() -> None:
    state = {
        "validation_result": {"passed": False, "errors": ["helm parse error"]},
        "fix_attempt": 1,
        "max_fix_attempts": 3,
        "event_kind": "github_ci_failure",
        "ci_fix_proposal": {"files": []},
    }
    assert route_after_validation(state) == "ci_fixer"

def test_route_after_validation_finalizes_on_exhausted_attempts() -> None:
    state = {
        "validation_result": {"passed": False, "errors": ["still broken"]},
        "fix_attempt": 3,
        "max_fix_attempts": 3,
        "event_kind": "github_ci_failure",
        "ci_fix_proposal": {"files": [{"path": "Helm/values-sbx.yaml", "content": "x: 1"}]},
    }
    assert route_after_validation(state) == "finalizer"

def test_route_after_validation_blocks_pr_on_failed_final_attempt() -> None:
    state = {
        "validation_result": {"passed": False, "errors": ["boom"]},
        "fix_attempt": 3,
        "max_fix_attempts": 3,
        "event_kind": "github_issue",
        "ci_fix_proposal": {"files": [{"path": "Helm/values-sbx.yaml", "content": "x: y"}]},
    }
    assert route_after_validation(state) == "finalizer"

class _LiveReadOnlyAdapter:
    """Wraps the real MCPAdapter for all read calls; blocks GitHub writes.

    All GitHub reads (get_job_logs, get_file_content, list_pull_requests, …)
    and all Kubernetes / Loki / Prometheus calls are routed to the real
    MCPAdapter unchanged.

    Any tool that belongs to _GITHUB_WRITE_TOOLS is intercepted: the call is
    recorded in ``github_write_calls`` but NOT forwarded to the real adapter,
    so the test can run against live data without touching GitHub state.
    """

    def __init__(self) -> None:
        from devops_agent.mcp_adapter import MCPAdapter as _RealMCPAdapter
        self._real = _RealMCPAdapter()
        self.github_write_calls: list[dict[str, Any]] = []

    def run(self, service: str, tool: str, params: dict, dry_run: bool = False) -> dict:
        if service == "github" and tool in _GITHUB_WRITE_TOOLS:
            self.github_write_calls.append({"tool": tool, "params": params})
            return _fake_github_write_response(tool)
        return self._real.run(service, tool, params, dry_run=dry_run)

def _build_live_ci_failure_state(branch: str) -> dict[str, Any]:
    """Initial AgentState for the live pipeline - no pre-fetched execution_results."""
    return {
        "request": (
            f"Fix CI failure on {TARGET_REPO} branch {branch} "
            f"(run #{_REAL_RUN_ID})"
        ),
        "dry_run": True,
        "event_kind": "github_ci_failure",
        "context": {
            "repo_full_name": TARGET_REPO,
            "branch": branch,
            "run_id": _REAL_RUN_ID,
            "run_url": _REAL_RUN_URL,
            "namespace": TARGET_NAMESPACE,
        },
        "execution_results": [],
        "fix_attempt": 0,
        "max_fix_attempts": 3,
        "validation_history": [],
    }

def _require_gh_cli() -> None:
    if not shutil.which("gh"):
        pytest.fail("gh CLI not found in PATH - required for live GitHub reads.")

@pytest.fixture(scope="module")
def live_pipeline_state(
    k8s_adapter: K8sAdapter,
    preflight_requirements: None,
) -> dict[str, Any]:
    """Run the CI-fix pipeline (ci_fixer → sandbox_validator → pr_creator) against
    real external systems using a pre-captured executor snapshot.

    The fixture is module-scoped so the expensive LLM + K8s + SMB validation
    runs only once; all five assertion tests share the same result snapshot.

    ``execution_results`` are loaded from
    ``tests/integration/fixtures/pr71_execution_results.json``  (a captured
    snapshot of the real executor output for PR #71 / run #25969747491).  This
    removes the dependency on live GitHub reads and the ``gh`` CLI while keeping
    the expensive LLM + SMB + K8s path fully live.

    Patches applied for the duration of this fixture:
      - ci_fixer._adapter, sandbox_validator._adapter
        → _LiveReadOnlyAdapter  (intercepts writes; routes reads to real adapters)
      - helm_rules._HELM_CHART_SOURCE → SMB_CHART_URI

    The _LiveReadOnlyAdapter instance is stored on the returned dict under the
    key ``"_live_adapter"`` so assertion tests can inspect github_write_calls.
    """
    import unittest.mock as _mock

    from devops_agent.nodes import ci_fixer as _ci_fixer_mod
    from devops_agent.nodes import sandbox_validator as _sandbox_mod
    from devops_agent.validators import helm_rules as _helm_rules_mod

    _require_openai_key()
    _uninstall_release_if_exists(k8s_adapter, _E2E_RELEASE_NAME)
    live_adapter = _LiveReadOnlyAdapter()

    patches = [
        _mock.patch.object(_ci_fixer_mod, "_adapter", live_adapter),
        _mock.patch.object(_sandbox_mod, "_adapter", live_adapter),
        _mock.patch.object(_helm_rules_mod, "_HELM_CHART_SOURCE", SMB_CHART_URI),
    ]
    for p in patches:
        p.start()

    try:
        execution_results = _load_live_execution_results()
        assert execution_results, (
            "Fixture pr71_execution_results.json is empty - regenerate with "
            "tests/integration/generate_live_fixture.py."
        )

        branch = TARGET_BRANCH
        state: dict[str, Any] = _build_live_ci_failure_state(branch)
        state["execution_results"] = execution_results
        state["plan"] = [
            {"tool": "github", "action": "get_job_logs",
             "args": {"repo": TARGET_REPO, "run_id": _REAL_RUN_ID, "job_id": 0}},
            {"tool": "github", "action": "get_file_content",
             "args": {"repo": TARGET_REPO, "path": "Helm/values-sbx.yaml",
                      "ref": branch}},
        ]

        state = ci_fixer_node(state)

        ts_suffix = str(int(time.time()))[-8:]
        state = {
            **state,
            "context": {
                **state.get("context", {}),
                "release_name": f"live-e2e-{ts_suffix}",
                "chart_type": "deployment",
            },
        }

        state = sandbox_validator_node(state)
        state = {**state, "dry_run": True}
        state = pr_creator_node(state)

    finally:
        for p in patches:
            p.stop()
        _uninstall_release_if_exists(k8s_adapter, _E2E_RELEASE_NAME)

    state
    state["_live_adapter"] = live_adapter
    return state

@pytest.mark.integration
def test_live_executor_fetches_real_ci_logs_from_github(
    live_pipeline_state: dict[str, Any],
) -> None:
    """Snapshot fixture must contain real job logs from GitHub run 25969747491.

    Checks that the log text contains at least one keyword that indicates a
    helm/probe/YAML failure - confirming the snapshot captures the right run.
    """
    execution_results: list[dict[str, Any]] = live_pipeline_state.get("execution_results", [])
    assert execution_results, "No execution_results in pipeline state."

    log_entries = [
        r for r in execution_results
        if r.get("action") == "get_job_logs" and r.get("tool") == "github"
    ]
    assert log_entries, (
        "No get_job_logs entry found in execution_results. "
        "Planner may not have included it, or executor skipped it."
    )

    logs_text = (log_entries[0].get("result") or {}).get("logs", "")
    assert logs_text, "get_job_logs returned empty logs from GitHub."

    failure_keywords = ("error", "fail", "probe", "yaml", "helm", "values-sbx", "invalid")
    assert any(kw in logs_text.lower() for kw in failure_keywords), (
        f"Job logs do not contain any expected failure keyword ({failure_keywords}). "
        f"First 400 chars: {logs_text[:400]!r}"
    )

    live_adapter: _LiveReadOnlyAdapter = live_pipeline_state["_live_adapter"]
    assert live_adapter.github_write_calls == [], (
        f"executor_node must not make GitHub write calls, got: {live_adapter.github_write_calls}"
    )

@pytest.mark.integration
def test_live_ci_fixer_proposes_helm_values_fix(
    live_pipeline_state: dict[str, Any],
) -> None:
    """ci_fixer_node must produce a non-empty proposal targeting Helm/values-sbx.yaml."""
    
    proposal: dict[str, Any] = live_pipeline_state.get("ci_fix_proposal") or {}

    assert proposal, "ci_fix_proposal is empty or missing from pipeline state."
    assert proposal.get("root_cause"), (
        f"Expected a non-empty root_cause in proposal, got: {proposal}"
    )

    files: list[dict[str, Any]] = proposal.get("files", [])
    assert files, f"ci_fixer proposed no file changes. proposal={proposal}"

    file_paths = [f.get("path", "") for f in files]
    assert "Helm/values-sbx.yaml" in file_paths, (
        f"Expected Helm/values-sbx.yaml in proposed files, got: {file_paths}. "
        "Check that job logs were successfully fetched and the LLM identified the correct file."
    )

@pytest.mark.integration
def test_live_sbx_validation_passes_with_smb_chart(
    live_pipeline_state: dict[str, Any],
) -> None:
    """sandbox_validator_node must confirm the ci_fixer proposal is deployable
    in iotag-sbx using the real SMB Helm chart (helm upgrade --dry-run)."""
    validation: dict[str, Any] = live_pipeline_state.get("validation_result") or {}

    assert validation, "validation_result is empty or missing from pipeline state."
    assert validation.get("passed") is True, (
        "sandbox_validator_node did not pass for the live ci_fixer proposal. "
        f"errors={validation.get('errors')} "
        f"output={validation.get('output', '')[:800]}"
    )

    output: str = validation.get("output", "")
    assert "helm deployability validation OK" in output, (
        f"Expected 'helm deployability validation OK' in validation output. Got: {output[:500]!r}"
    )

    details: list[dict[str, Any]] = validation.get("details", [])
    assert any(d.get("patchable") is True for d in details), (
        f"Expected at least one patchable=True entry in validation details. Got: {details}"
    )

@pytest.mark.integration
def test_live_pr_creator_dry_run_summary_no_writes(
    live_pipeline_state: dict[str, Any],
) -> None:
    """pr_creator_node(dry_run=True) must produce a [DRY RUN] summary and
    must NOT have called any GitHub write API anywhere in the pipeline."""
    final_summary: str = live_pipeline_state.get("final_summary", "")
    assert "[DRY RUN]" in final_summary, (
        f"Expected '[DRY RUN]' in final_summary, got: {final_summary!r}"
    )

    pr_url = live_pipeline_state.get("pr_url")
    assert pr_url is None, (
        f"pr_url should be None in dry_run mode - no real PR should be created. Got: {pr_url!r}"
    )

    live_adapter: _LiveReadOnlyAdapter = live_pipeline_state["_live_adapter"]
    assert live_adapter.github_write_calls == [], (
        "GitHub write calls were made during the pipeline even though dry_run=True. "
        f"Calls recorded: {live_adapter.github_write_calls}"
    )

@pytest.mark.integration
def test_live_pipeline_route_leads_to_pr_creator(
    live_pipeline_state: dict[str, Any],
) -> None:
    """After a passing validation the router must direct to pr_creator.

    This validates the full conditional chain: real ci_fixer proposal +
    real validation passed → route_after_validation returns 'pr_creator'.
    """

    route = route_after_validation(live_pipeline_state)
    assert route == "pr_creator", (
        f"Expected route 'pr_creator' after successful live validation, got {route!r}. "
        f"validation_result={live_pipeline_state.get('validation_result')} "
        f"fix_attempt={live_pipeline_state.get('fix_attempt')}"
    )
