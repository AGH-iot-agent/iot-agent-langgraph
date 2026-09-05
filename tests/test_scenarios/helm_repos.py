"""Target-repo catalog for scenario tests (thesis: run per Helm service repo).

Only sibling workspaces that actually ship ``Helm/values-*.yaml`` are treated as
helm-backed. ``TEST_SCENARIO_REPOS`` selects which of those (or ``all``) to run.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_HOME = Path(os.environ.get("TEST_SCENARIO_HOME", "/home/dominiq"))
_SCENARIOS_DIR = Path(__file__).resolve().parent

# scenario_id → GitHub repo basename (must match Helm chart / k8s service name)
SCENARIO_TARGET_REPO: dict[str, str] = {
    "01": "iot-agent-login-screen",
    "02": "iot-agent-login-screen",
    "03": "iot-agent-authentication",
    "04": "iot-agent-gateway-api",
    "05": "iot-agent-alert-api",
    "06": "iot-agent-logs",
    "07": "iot-agent-login-screen",
    "08": "iot-agent-authentication",
    "09": "iot-agent-authentication",
    "10": "iot-agent-logs",
    "11": "iot-agent-alert-api",
    "12": "iot-agent-dashboard-api",
    "13": "iot-agent-login-screen",
    "14": "iot-agent-authentication",
    "15": "iot-agent-login-screen",
    "16": "iot-agent-login-screen",
    "17": "iot-agent-login-screen",
    "18": "iot-agent-gateway-api",
    "19": "iot-agent-login-screen",
    "20": "iot-agent-login-screen",
}

_SKIP_DIR_NAMES = {
    "actions-runner",
    ".venv",
    "node_modules",
    "Universal-Kubernetes-Helm-Charts",
}

_REPO_FROM_NODEID = re.compile(r"scenario[_]?(\d+)", re.IGNORECASE)


def discover_helm_repos(home: Path | None = None) -> dict[str, Path]:
    """Return ``{repo_basename: workspace_path}`` for dirs with Helm/values-*.yaml."""
    root = home or _HOME
    found: dict[str, Path] = {}
    if not root.is_dir():
        return found
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in _SKIP_DIR_NAMES or child.name.startswith("."):
            continue
        helm = child / "Helm"
        if not helm.is_dir():
            continue
        values = list(helm.glob("values-*.yaml")) + list(helm.glob("values-*.yml"))
        if values:
            found[child.name] = child
    return found


def helm_repo_basenames(home: Path | None = None) -> set[str]:
    return set(discover_helm_repos(home))


def scenario_id_from_nodeid(nodeid: str) -> str | None:
    match = _REPO_FROM_NODEID.search(nodeid.replace("\\", "/"))
    if not match:
        return None
    return match.group(1).zfill(2)


def marker_for_repo(repo_basename: str) -> str:
    slug = repo_basename.removeprefix("iot-agent-").replace("-", "_")
    return f"repo_{slug}"


def parse_repo_filter(raw: str | None = None) -> set[str] | None:
    """None = run every scenario. Otherwise a set of repo basenames.

    ``helm`` → only sibling workspaces that have Helm values.
    ``all`` / empty → no filter.
    Comma-separated names accept short (``login-screen``) or full
    (``iot-agent-login-screen``) forms.
    """
    text = (raw if raw is not None else os.environ.get("TEST_SCENARIO_REPOS", "")).strip()
    if not text or text.lower() in {"all", "*"}:
        return None
    helm_known = helm_repo_basenames()
    if text.lower() == "helm":
        return helm_known
    selected: set[str] = set()
    for part in text.replace(";", ",").split(","):
        name = part.strip().strip("/")
        if not name:
            continue
        if name.lower() == "helm":
            selected.update(helm_known)
            continue
        if name in helm_known or name in SCENARIO_TARGET_REPO.values():
            selected.add(name)
            continue
        prefixed = name if name.startswith("iot-agent-") else f"iot-agent-{name}"
        selected.add(prefixed)
    return selected


def scenario_allowed(scenario_id: str, selected_repos: set[str] | None) -> bool:
    if selected_repos is None:
        return True
    target = SCENARIO_TARGET_REPO.get(str(scenario_id).zfill(2), "")
    return bool(target) and target in selected_repos


def selected_scenario_ids() -> set[str] | None:
    repos = parse_repo_filter()
    if repos is None:
        return None
    return {
        sid
        for sid, target in SCENARIO_TARGET_REPO.items()
        if scenario_allowed(sid, repos)
    }
