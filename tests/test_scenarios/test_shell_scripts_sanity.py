from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_no_legacy_env_loading_pattern_in_scenarios() -> None:
    legacy = "export $(grep -v '^#' .env | xargs)"
    offenders: list[str] = []

    for script in sorted(ROOT.glob("scenario_*/scenario_*.sh")):
        if legacy in script.read_text(encoding="utf-8"):
            offenders.append(str(script.relative_to(ROOT.parent.parent)))

    assert not offenders, (
        "Legacy .env loader is forbidden because it breaks nested expansions "
        f"and can mask errors. Offenders: {offenders}"
    )


def test_issue_scripts_fail_on_http_error() -> None:
    # These scripts create GitHub issues and should fail hard on API errors.
    for rel in (
        "scenario_04/scenario_04.sh",
        "scenario_06/scenario_06.sh",
        "scenario_08/scenario_08.sh",
        "scenario_09/scenario_09.sh",
    ):
        src = _read(rel)
        assert "HTTP_CODE=$(curl" in src
        assert "GitHub API returned HTTP" in src
        assert "exit 1" in src


def test_pr_scripts_use_unique_workdirs_and_cleanup() -> None:
    for rel, prefix in (
        ("scenario_10/scenario_10.sh", "scenario_10_repo_"),
        ("scenario_12/scenario_12.sh", "scenario_12_repo_"),
    ):
        src = _read(rel)
        assert f"WORKDIR=\"/tmp/{prefix}" in src
        assert "trap cleanup EXIT" in src
        assert "git clone \"https://${GH_TOKEN}@github.com/${OWNER}/${REPO}.git\" \"$WORKDIR\"" in src


def test_launcher_supports_continue_on_error() -> None:
    src = (ROOT / "run_scenario_actions.sh").read_text(encoding="utf-8")
    assert "--continue-on-error" in src
    assert "Scenarios with failures:" in src
