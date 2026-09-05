from __future__ import annotations

from pathlib import Path

from devops_agent.monitors.github_pr_build_monitor import resolve_linked_pull_requests

SCENARIOS = Path(__file__).resolve().parent


class _FakeAdapter:
    def __init__(self, prs: list[dict]) -> None:
        self.prs = prs
        self.calls: list[tuple[str, str, dict]] = []

    def run(self, service: str, action: str, args: dict, dry_run: bool = False) -> dict:
        self.calls.append((service, action, args))
        return {"status": "ok", "pull_requests": self.prs}


def test_resolve_linked_pull_requests_keeps_api_list_when_present() -> None:
    adapter = _FakeAdapter([{"number": 99}])
    run = {"id": 1, "head_branch": "test/scenario_17_1", "pull_requests": [{"number": 7}]}
    assert resolve_linked_pull_requests(adapter, "org/repo", run) == [{"number": 7}]
    assert adapter.calls == []


def test_resolve_linked_pull_requests_matches_head_branch_when_api_empty() -> None:
    adapter = _FakeAdapter(
        [
            {"number": 3, "head": {"ref": "other"}, "state": "open"},
            {"number": 4, "head": {"ref": "test/scenario_17_1"}, "state": "open", "base": {"ref": "main"}},
        ]
    )
    run = {"id": 9, "head_branch": "test/scenario_17_1", "pull_requests": []}
    matched = resolve_linked_pull_requests(adapter, "org/repo", run)
    assert [pr["number"] for pr in matched] == [4]
    assert adapter.calls[0][1] == "list_pull_requests"


def test_resolve_linked_pull_requests_skips_protected_branches() -> None:
    adapter = _FakeAdapter([{"number": 1, "head": {"ref": "main"}}])
    run = {"id": 2, "head_branch": "main", "pull_requests": []}
    assert resolve_linked_pull_requests(adapter, "org/repo", run) == []
    assert adapter.calls == []


def test_setup_scripts_use_supported_github_api_version() -> None:
    forbidden = []
    missing_helper = []
    for path in SCENARIOS.glob("scenario_*/scenario_*.sh"):
        text = path.read_text(encoding="utf-8")
        if "2026-03-10" in text:
            forbidden.append(path.name)
        if "X-GitHub-Api-Version:" in text and "2022-11-28" not in text:
            forbidden.append(path.name)
        if path.name in {
            "scenario_05.sh",
            "scenario_06.sh",
            "scenario_08.sh",
            "scenario_09.sh",
            "scenario_10.sh",
            "scenario_12.sh",
            "scenario_01.sh",
        }:
            if path.name != "scenario_01.sh" and "_github_setup_token.sh" not in text:
                missing_helper.append(path.name)
    helper = (SCENARIOS / "_github_setup_token.sh").read_text(encoding="utf-8")
    assert "2022-11-28" in helper
    assert "scenario-local" in helper or "Do not source scenario-local" in helper
    assert not forbidden, f"scripts still use an invalid GitHub API version: {forbidden}"
    assert not missing_helper, f"scripts missing token helper: {missing_helper}"
