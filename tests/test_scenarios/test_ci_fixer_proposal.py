from __future__ import annotations

from devops_agent.nodes.ci_fixer import _coerce_proposal_files, _filter_files
from devops_agent.validators.orchestrator import _proposal_file_entries


def test_coerce_top_level_path_content_into_files() -> None:
    proposal = _coerce_proposal_files(
        {"path": "Helm/values-sbx.yaml", "content": "name: login\n"}
    )
    assert proposal["files"] == [
        {"path": "Helm/values-sbx.yaml", "content": "name: login\n"}
    ]
    assert "path" not in proposal
    assert "content" not in proposal


def test_coerce_files_dict_into_list() -> None:
    proposal = _coerce_proposal_files(
        {"files": {"path": "Helm/values-sbx.yaml", "content": "x: 1"}}
    )
    assert proposal["files"] == [{"path": "Helm/values-sbx.yaml", "content": "x: 1"}]


def test_filter_files_keeps_coerced_helm_values() -> None:
    proposal = _filter_files(
        {"path": "Helm/values-sbx.yaml", "content": "name: login\n"},
        real_paths=["Helm/values-sbx.yaml"],
        issue_type="helm_manifest",
    )
    assert proposal["files"]
    assert proposal["files"][0]["path"] == "Helm/values-sbx.yaml"


def test_proposal_file_entries_from_top_level_dict() -> None:
    files = _proposal_file_entries({"path": "Helm/values-sbx.yaml", "content": "x: 1"})
    assert files[0]["path"] == "Helm/values-sbx.yaml"
    assert files[0]["content"] == "x: 1"
