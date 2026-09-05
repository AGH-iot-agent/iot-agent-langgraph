"""Root test fixtures: load `.env` and normalize tokens for every pytest entry."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def pytest_configure(config) -> None:  # noqa: ARG001
    load_dotenv(_PROJECT_ROOT / ".env")
    agent_token = (
        os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or ""
    ).strip()
    if agent_token:
        os.environ.setdefault("GH_TOKEN", agent_token)
        os.environ.setdefault("GITHUB_TOKEN", agent_token)

    non_agent_token = (
        os.environ.get("GH_NON_AGENT_TOKEN")
        or os.environ.get("TEST_O1_ENV_GH_TOKEN")
        or ""
    ).strip()
    if non_agent_token:
        os.environ.setdefault("GH_NON_AGENT_TOKEN", non_agent_token)
        os.environ.setdefault("TEST_O1_ENV_GH_TOKEN", non_agent_token)

    if not os.environ.get("KUBECONFIG"):
        os.environ["KUBECONFIG"] = "/etc/rancher/k3s/k3s.yaml"


def pytest_ignore_collect(collection_path, config) -> bool:  # noqa: ARG001
    path = Path(collection_path)
    if path.name.endswith(".old.py"):
        return True
    return False
