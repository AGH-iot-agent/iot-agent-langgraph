from __future__ import annotations

import logging
from typing import Any

from devops_agent.mcp_adapter import MCPAdapter
from devops_agent.state import AgentState

logger = logging.getLogger(__name__)

_adapter = MCPAdapter()


def sandbox_validator_node(state: AgentState) -> AgentState:
    """
        Validate the CI fix proposal against the iotag-sbx namespace before opening a PR.

        For each changed file:
        - If it's a Helm values file: render the chart with the new values, then apply dry-run
        - If it's a raw manifest YAML: apply dry-run directly

        Stores result in state['validation_result'].
    """
    proposal: dict[str, Any] = state.get("ci_fix_proposal") or {}
    files: list[dict[str, Any]] = proposal.get("files", [])
    context: dict[str, Any] = state.get("context", {})
    namespace: str = context.get("namespace", "iotag-sbx")
    service_name: str = context.get("repo_full_name", "").split("/")[-1]

    if not files:
        return {
            **state,
            "validation_result": {
                "passed": False,
                "errors": ["No files in fix proposal — nothing to validate."],
                "output": "",
            },
        }

    errors: list[str] = []
    outputs: list[str] = []

    for file_entry in files:
        path: str = file_entry.get("path", "")
        content: str = (
            file_entry.get("content")
            or file_entry.get("fixed_snippet")
            or ""
        )

        if not content:
            errors.append(f"{path}: empty content — skipping")
            continue

        if "values" in path and path.endswith((".yaml", ".yml")):
            logger.info("[SANDBOX] Rendering Helm template for %s with %s", service_name, path)
            render_result = _adapter.run(
                "kubernetes", "helm_template_render",
                {
                    "name": service_name,
                    "chart": f"Universal-Kubernetes-Helm-Charts/charts/deployment",
                    "values_override": content,
                },
                dry_run=False,
            )
            if render_result.get("status") != "ok":
                errors.append(f"helm template failed for {path}: {render_result.get('message', '')}")
                continue

            rendered_manifests: str = render_result.get("manifests", "")
            outputs.append(f"helm template OK for {path}")

            if rendered_manifests:
                logger.info("[SANDBOX] Applying dry-run for %s in %s", path, namespace)
                apply_result = _adapter.run(
                    "kubernetes", "apply_manifest_dryrun",
                    {"manifest_yaml": rendered_manifests, "namespace": namespace},
                    dry_run=False,
                )
                if apply_result.get("status") != "ok":
                    errors.append(f"kubectl apply --dry-run failed for {path}: {apply_result.get('message', '')}")
                else:
                    outputs.append(f"kubectl apply --dry-run=server OK for {path}")

        elif path.endswith((".yaml", ".yml")):
            logger.info("[SANDBOX] Applying dry-run for raw manifest %s in %s", path, namespace)
            apply_result = _adapter.run(
                "kubernetes", "apply_manifest_dryrun",
                {"manifest_yaml": content, "namespace": namespace},
                dry_run=False,
            )
            if apply_result.get("status") != "ok":
                errors.append(f"kubectl apply --dry-run failed for {path}: {apply_result.get('message', '')}")
            else:
                outputs.append(f"kubectl apply --dry-run=server OK for {path}")

        else:
            outputs.append(f"{path}: non-YAML file — skipping k8s validation")

    passed = len(errors) == 0
    validation_result = {
        "passed": passed,
        "errors": errors,
        "output": "\n".join(outputs),
    }

    logger.info("[SANDBOX] validation passed=%s errors=%d", passed, len(errors))

    return {
        **state,
        "validation_result": validation_result,
    }


def route_after_validation(state: AgentState) -> str:
    """Route based on validation result and event kind.

    - PR build failure event → pr_fix_commenter (comment on existing PR)
    - CI failure or github_issue event → pr_creator regardless of validation
      (validation failure is noted in PR body as a warning, not a blocker)
    - Other events with failed validation → finalizer
    """
    event_kind = state.get("event_kind", "")
    if event_kind == "github_pr_build_failure":
        return "pr_fix_commenter"
    if event_kind in ("github_ci_failure", "github_issue", "github_pr"):
        proposal = state.get("ci_fix_proposal") or {}
        if not proposal.get("files"):
            return "finalizer"  # nothing to commit
        return "pr_creator"
    result = state.get("validation_result") or {}
    if not result.get("passed"):
        return "finalizer"
    return "pr_creator"
