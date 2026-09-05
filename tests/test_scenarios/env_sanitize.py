"""Normalize scenario env aliases and quoted dotenv values.

``.env`` entries like ``TEST_CI_FIX_TARGET_NAMESPACE="iotag-sbx"`` keep the
quotes in ``os.environ``, so kubectl/helm see a namespace that does not exist.
Tokens are never rewritten here except GH_TOKEN ↔ GITHUB_TOKEN aliasing.
"""
from __future__ import annotations

import os

_QUOTE_STRIP_KEYS = {
    "KUBECONFIG",
    "KUBERNETES_NAMESPACE",
    "WATCHDOG_DEFAULT_NAMESPACE",
    "GITHUB_ORG",
    "TEST_SMB_CHART_URI",
}

_QUOTE_STRIP_SUFFIXES = (
    "_NAMESPACE",
    "_KUBECONFIG",
    "_TARGET_REPO",
    "_REPOSITORY",
    "_CHART_URI",
    "_URI",
    "_BRANCH",
    "_PATH",
)

_DEFAULT_KUBECONFIG = "/etc/rancher/k3s/k3s.yaml"


def _strip_wrapping_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def _should_strip_quotes(key: str) -> bool:
    if key in _QUOTE_STRIP_KEYS:
        return True
    if key.startswith("TEST_") and key.endswith(_QUOTE_STRIP_SUFFIXES):
        return True
    return False


def sanitize_scenario_environ(environ: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ if environ is None else environ
    for key in list(env):
        if not _should_strip_quotes(key):
            continue
        raw = env.get(key)
        if not raw:
            continue
        stripped = _strip_wrapping_quotes(raw)
        if stripped != raw:
            env[key] = stripped

    gh = (env.get("GH_TOKEN") or "").strip()
    github = (env.get("GITHUB_TOKEN") or "").strip()
    if gh and not github:
        env["GITHUB_TOKEN"] = gh
    elif github and not gh:
        env["GH_TOKEN"] = github

    if not (env.get("KUBECONFIG") or "").strip():
        env["KUBECONFIG"] = _DEFAULT_KUBECONFIG
    return env
