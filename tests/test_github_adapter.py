from __future__ import annotations

import subprocess

from devops_agent.adapters.github_adapter import GHAdapter
from devops_agent.adapters.loki_adapter import LokiAdapter
from devops_agent.mcp_adapter import MCPAdapter


def test_list_org_repos_fails_cleanly_when_gh_not_authenticated(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    calls = []

    def fake_run(cmd, capture_output, text, env, timeout=None):
        calls.append(cmd)
        if cmd[:2] == ["gh", "auth"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not logged in")
        raise AssertionError(f"unexpected gh command invoked: {cmd}")

    monkeypatch.setattr("devops_agent.adapters.github_adapter.subprocess.run", fake_run)

    result = GHAdapter().list_org_repos("AGH-iot-agent")

    assert result["status"] == "error"
    assert "not logged in" in result["message"] or "gh auth login" in result["message"]
    assert calls and calls[0][:2] == ["gh", "auth"]


def test_loki_query_range_returns_clean_error_without_base_url():
    result = LokiAdapter(base_url="").query_range(query="{namespace=\"iot-agent\"}")

    assert result["status"] == "error"
    assert "LOKI_URL" in result["message"]


def test_mcp_adapter_surfaces_runtime_error_cleanly():
    class FailingTool:
        def __call__(self, **kwargs):
            raise RuntimeError("You are not logged into any GitHub hosts. To log in, run: gh auth login")

    adapter = MCPAdapter()
    adapter._registry = {"github": {"broken": FailingTool()}}

    result = adapter.run("github", "broken", {"repo": "AGH-iot-agent"}, dry_run=False)

    assert result["status"] == "error"
    assert "gh auth login" in result["message"]
