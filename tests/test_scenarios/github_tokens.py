"""Tokens for scenario fixtures vs the agent bot.

``GH_TOKEN`` / ``GITHUB_TOKEN`` remain the agent (bot) credentials.
Fixture issues and PRs should be created with ``GH_NON_AGENT_TOKEN``
(or ``TEST_O1_ENV_GH_TOKEN``) so the bot is not the author of the trigger.

If the non-agent PAT is missing or unauthorized (HTTP 401), fixture setup
falls back to ``GH_TOKEN`` so scenario scripts still run. The issue monitor
does not skip bot-authored issues.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

from devops_agent.adapters.github_adapter import GHAdapter

logger = logging.getLogger(__name__)

_GITHUB_API_VERSION = "2022-11-28"


def _probe_github_token(token: str) -> bool:
    token = (token or "").strip()
    if not token:
        return False
    try:
        import httpx

        response = httpx.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": _GITHUB_API_VERSION,
                "User-Agent": "iot-agent-scenarios",
            },
            timeout=10.0,
        )
        return response.status_code == 200
    except Exception:
        logger.warning("GitHub token probe failed", exc_info=True)
        return False


@lru_cache(maxsize=8)
def _cached_probe(token: str) -> bool:
    return _probe_github_token(token)


def fixture_github_token() -> str:
    """Working PAT for creating/closing fixture issues and PRs."""
    preferred = [
        os.environ.get("GH_NON_AGENT_TOKEN", ""),
        os.environ.get("TEST_O1_ENV_GH_TOKEN", ""),
    ]
    fallback = [
        os.environ.get("GH_TOKEN", ""),
        os.environ.get("GITHUB_TOKEN", ""),
    ]
    seen: set[str] = set()
    for raw in preferred:
        token = raw.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        if _cached_probe(token):
            return token
        logger.warning(
            "GH_NON_AGENT_TOKEN/TEST_O1_ENV_GH_TOKEN is set but GitHub returned unauthorized; "
            "will try GH_TOKEN for fixture setup"
        )
    for raw in fallback:
        token = raw.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        if _cached_probe(token):
            logger.warning(
                "Using GH_TOKEN for scenario fixtures because the non-agent PAT is missing "
                "or unauthorized"
            )
            return token
    raise RuntimeError(
        "No working GitHub token for scenario fixtures. Set GH_NON_AGENT_TOKEN "
        "(preferred) or GH_TOKEN. The current non-agent PAT was unauthorized (HTTP 401)."
    )


def non_agent_github_token() -> str:
    """Backward-compatible name used by scenario tests and setup scripts."""
    return fixture_github_token()


def non_agent_gh_adapter() -> GHAdapter:
    return GHAdapter(token=non_agent_github_token())


def github_setup_env() -> dict[str, str]:
    """Subprocess env for .sh setup/cleanup: git/curl/gh see a working PAT."""
    env = os.environ.copy()
    token = fixture_github_token()
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token
    env["TEST_O1_ENV_GH_TOKEN"] = token
    env["GH_NON_AGENT_TOKEN"] = token
    env.setdefault("GIT_AUTHOR_NAME", "iot-agent-scenario")
    env.setdefault("GIT_AUTHOR_EMAIL", "scenario-tests@iot-agent.local")
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("KUBECONFIG", "/etc/rancher/k3s/k3s.yaml")
    env["AGENT_MODE"] = os.environ.get("AGENT_MODE", "multi_agent")
    if env.get("GH_TOKEN") and not env.get("GITHUB_TOKEN"):
        env["GITHUB_TOKEN"] = env["GH_TOKEN"]
    elif env.get("GITHUB_TOKEN") and not env.get("GH_TOKEN"):
        env["GH_TOKEN"] = env["GITHUB_TOKEN"]
    return env


def reset_token_probe_cache() -> None:
    clearer = getattr(_cached_probe, "cache_clear", None)
    if callable(clearer):
        clearer()
