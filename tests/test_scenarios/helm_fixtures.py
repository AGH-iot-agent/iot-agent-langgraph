"""Surgical mutations of real Helm values files for scenario PRs.

Replacing the whole chart with a stripped skeleton makes original_snippet
unpatchable (wrong indent/quoting/file). Fixtures start from the sibling
workspace file (or a checked-in copy) and change only the injected fault.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.test_scenarios.helm_repos import discover_helm_repos

_SCENARIOS = Path(__file__).resolve().parent
_REPLICA_RE = re.compile(r"^replicaCount:\s*.+$", re.MULTILINE)
_LIVENESS_PATH_RE = re.compile(
    r"(livenessProbe:(?:.*\n)*?^\s*)path:\s*\S+",
    re.MULTILINE,
)


def helm_values_path(repo_basename: str, env: str) -> Path | None:
    repos = discover_helm_repos()
    workspace = repos.get(repo_basename)
    if workspace is None:
        return None
    candidate = workspace / "Helm" / f"values-{env}.yaml"
    return candidate if candidate.is_file() else None


def load_real_values(repo_basename: str, env: str, *, fallback: Path | None = None) -> str:
    live = helm_values_path(repo_basename, env)
    if live is not None:
        return live.read_text(encoding="utf-8")
    if fallback is not None and fallback.is_file():
        return fallback.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"No Helm/values-{env}.yaml for {repo_basename} "
        f"(looked in sibling workspaces and {fallback})"
    )


def set_replica_count(text: str, value: str) -> str:
    """Set replicaCount. Pass a quoted string to inject a Helm type error."""
    if not _REPLICA_RE.search(text):
        raise ValueError("replicaCount: line not found in Helm values")
    return _REPLICA_RE.sub(f"replicaCount: {value}", text, count=1)


def set_liveness_path(text: str, path: str) -> str:
    match = _LIVENESS_PATH_RE.search(text)
    if not match:
        raise ValueError("livenessProbe path: not found in Helm values")
    return text[: match.start()] + match.group(1) + f"path: {path}" + text[match.end() :]


def inject_scenario_02(base_sbx: str) -> str:
    """Invalid replicaCount type + bad liveness path on the REAL values-sbx file."""
    mutated = set_replica_count(base_sbx, '"not-a-number"')
    return set_liveness_path(mutated, "/invalid-path-for-liveness-probe")


def scenario_02_broken_sbx() -> str:
    fallback = _SCENARIOS / "scenario_02" / "values-sbx.yaml"
    base = load_real_values("iot-agent-login-screen", "sbx", fallback=fallback)
    if 'replicaCount: "not-a-number"' in base and "/invalid-path-for-liveness-probe" in base:
        return base
    return inject_scenario_02(base)
