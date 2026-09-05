from __future__ import annotations

import tests.test_scenarios.github_tokens as github_tokens


def test_fixture_token_falls_back_to_gh_token_when_non_agent_unauthorized(monkeypatch) -> None:
    github_tokens.reset_token_probe_cache()
    monkeypatch.setenv("GH_NON_AGENT_TOKEN", "ghp_invalid_non_agent")
    monkeypatch.setenv("TEST_O1_ENV_GH_TOKEN", "ghp_invalid_non_agent")
    monkeypatch.setenv("GH_TOKEN", "ghp_valid_bot_token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def fake_probe(token: str) -> bool:
        return token == "ghp_valid_bot_token"

    monkeypatch.setattr(github_tokens, "_probe_github_token", fake_probe)
    # cache wraps _probe_github_token at decoration time; clear and bypass cache
    monkeypatch.setattr(github_tokens, "_cached_probe", fake_probe)

    token = github_tokens.fixture_github_token()
    assert token == "ghp_valid_bot_token"

    env = github_tokens.github_setup_env()
    assert env["GH_TOKEN"] == "ghp_valid_bot_token"
    assert env["GH_NON_AGENT_TOKEN"] == "ghp_valid_bot_token"
    assert env["GITHUB_TOKEN"] == "ghp_valid_bot_token"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["KUBECONFIG"]
    assert "AGENT_MODE" in env
    github_tokens.reset_token_probe_cache()


def test_fixture_token_prefers_working_non_agent(monkeypatch) -> None:
    github_tokens.reset_token_probe_cache()
    monkeypatch.setenv("GH_NON_AGENT_TOKEN", "ghp_human")
    monkeypatch.setenv("GH_TOKEN", "ghp_bot")

    def fake_probe(token: str) -> bool:
        return token in {"ghp_human", "ghp_bot"}

    monkeypatch.setattr(github_tokens, "_cached_probe", fake_probe)
    assert github_tokens.non_agent_github_token() == "ghp_human"
    github_tokens.reset_token_probe_cache()
