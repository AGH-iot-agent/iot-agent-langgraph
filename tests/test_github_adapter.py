from __future__ import annotations

import subprocess

from devops_agent.adapters.github_adapter import GHAdapter


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
