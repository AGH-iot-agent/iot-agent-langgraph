"""Session metrics, logs, and collection rules for tests/test_scenarios."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

from tests.test_scenarios import run_metrics
from tests.test_scenarios.agent_modes import apply_agent_mode, apply_guardrails_enabled, scenario_agent_modes
from tests.test_scenarios.env_sanitize import sanitize_scenario_environ
from tests.test_scenarios.helm_repos import (
    SCENARIO_TARGET_REPO,
    helm_repo_basenames,
    marker_for_repo,
    scenario_id_from_nodeid,
    selected_scenario_ids,
)

_SCENARIOS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCENARIOS_DIR.parent.parent
_REPO_LOGS = _REPO_ROOT / "logs"
_LOGS_DIR = _SCENARIOS_DIR / "logs"
_REPORTS_DIR = _SCENARIOS_DIR / "reports"
_logger = logging.getLogger("tests.test_scenarios")
_AGENT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _running_single_scenario(config: pytest.Config) -> bool:
    for arg in config.args:
        name = Path(str(arg).replace("\\", "/")).name
        if name.startswith("test_"):
            continue
        if name.startswith("scenario_") and name.endswith(".py"):
            return True
    return False


def _collecting_scenarios_folder(config: pytest.Config) -> bool:
    """True when pytest was given the test_scenarios directory (All scenarios)."""
    if _running_single_scenario(config):
        return False
    for arg in config.args:
        text = str(arg).replace("\\", "/").rstrip("/")
        if text.endswith("test_scenarios"):
            return True
        parts = [p for p in text.split("/") if p]
        if parts and parts[-1].startswith("scenario_"):
            return False
    return False


def _under_dir(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except (ValueError, OSError):
        return False


def resolve_scenario_pytest_log_file(log_file: str, logs_dir: Path | None = None) -> Path:
    """Map pytest log_file onto tests/test_scenarios/logs/ unless it is already there."""
    dest_dir = logs_dir or _LOGS_DIR
    text = str(log_file or "").strip().replace("\\", "/")
    if not text:
        return dest_dir / "pytest.log"
    path = Path(text)
    if not path.is_absolute():
        path = (_REPO_ROOT / path)
    if _under_dir(path, dest_dir):
        return path
    name = path.name if path.suffix == ".log" else "pytest.log"
    return dest_dir / name


def redirect_agent_file_log(logs_dir: Path | None = None) -> Path:
    """Send devops_agent FileHandler output to tests/test_scenarios/logs/agent.log."""
    dest = (logs_dir or _LOGS_DIR) / "agent.log"
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.environ["DEVOPS_AGENT_LOG_FILE"] = str(dest)
    os.environ["DEVOPS_AGENT_LOG_DIR"] = str(dest.parent)
    dest_resolved = dest.resolve()
    root = logging.getLogger()
    formatter = logging.Formatter(_AGENT_LOG_FORMAT)
    for handler in list(root.handlers):
        if not isinstance(handler, logging.FileHandler):
            continue
        base = Path(getattr(handler, "baseFilename", "") or "")
        if not str(base):
            continue
        try:
            resolved = base.resolve()
        except OSError:
            continue
        if resolved == dest_resolved:
            continue
        if not _under_dir(resolved, _REPO_LOGS):
            continue
        root.removeHandler(handler)
        handler.close()
    if not any(
        isinstance(handler, logging.FileHandler)
        and Path(getattr(handler, "baseFilename", "")).resolve() == dest_resolved
        for handler in root.handlers
    ):
        file_handler = logging.FileHandler(dest, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    for handler in list(root.handlers):
        if not isinstance(handler, logging.StreamHandler):
            continue
        if isinstance(handler, logging.FileHandler):
            continue
        if getattr(handler, "stream", None) in {sys.stdout, sys.stderr}:
            root.removeHandler(handler)
    return dest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    sanitize_scenario_environ()
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    args_text = " ".join(str(a).replace("\\", "/") for a in config.args)
    in_scenarios = "test_scenarios" in args_text or _collecting_scenarios_folder(config)
    if not in_scenarios:
        return

    logging.getLogger("httpx").setLevel(logging.WARNING)
    log_path = _LOGS_DIR / "pytest.log"
    raw = str(getattr(config.option, "log_file", None) or "")
    if not raw.strip():
        try:
            raw = str(config.getini("log_file") or "")
        except ValueError:
            raw = ""
    chosen = resolve_scenario_pytest_log_file(raw)
    config.option.log_file = str(chosen)
    chosen.parent.mkdir(parents=True, exist_ok=True)
    agent_log = redirect_agent_file_log()

    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s:%(lineno)d  %(message)s")
    )
    logging.getLogger().addHandler(handler)
    config._scenario_log_handler = handler  # type: ignore[attr-defined]
    _logger.info("scenario file log -> %s agent.log -> %s", chosen, agent_log)


def pytest_unconfigure(config: pytest.Config) -> None:
    handler = getattr(config, "_scenario_log_handler", None)
    if handler is None:
        return
    logging.getLogger().removeHandler(handler)
    handler.close()


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize live scenario tests with SCENARIO_AGENT_MODES (default both)."""
    if "agent_mode" not in metafunc.fixturenames:
        return
    for marker in metafunc.definition.iter_markers(name="parametrize"):
        argnames = marker.args[0] if marker.args else ""
        if "agent_mode" in str(argnames).replace(" ", "").split(","):
            return
    metafunc.parametrize("agent_mode", scenario_agent_modes(), ids=lambda mode: str(mode))


def pytest_sessionstart(session: pytest.Session) -> None:
    sanitize_scenario_environ()
    _logger.info(
        "scenario session start args=%s log_file=%s RUN_SCENARIO_E2E=%s "
        "AGENT_MODE=%s SCENARIO_AGENT_MODES=%s",
        [str(a) for a in session.config.args],
        getattr(session.config.option, "log_file", None),
        os.environ.get("RUN_SCENARIO_E2E"),
        os.environ.get("AGENT_MODE"),
        os.environ.get("SCENARIO_AGENT_MODES"),
    )
    if _is_truthy(os.environ.get("RUN_SCENARIO_E2E")) or _running_single_scenario(session.config):
        try:
            from tests.test_scenarios.cluster_cleanup import uninstall_leftover_test_releases

            uninstall_leftover_test_releases()
        except Exception as exc:  # noqa: BLE001 — best-effort pre-run cleanup
            _logger.warning("leftover helm cleanup skipped: %s", exc)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Prefer scenario_XX.py for All scenarios; skip live tests without the E2E flag."""
    collecting_folder = _collecting_scenarios_folder(config)
    e2e_enabled = _is_truthy(os.environ.get("RUN_SCENARIO_E2E"))
    single = _running_single_scenario(config)
    skip_live = pytest.mark.skip(
        reason="Set RUN_SCENARIO_E2E=1 to run live scenario tests "
        "(or launch a single scenario_XX.py)"
    )
    skip_duplicate_01 = pytest.mark.skip(
        reason="All scenarios uses test_scenario01_real_agent_comments_on_live_issue; "
        "the recording-adapter tick path is not a thesis row"
    )

    skip_repo = pytest.mark.skip(
        reason="Skipped by TEST_SCENARIO_REPOS (helm-backed sibling workspaces only, "
        "or an explicit repo filter). Set TEST_SCENARIO_REPOS=all for every scenario."
    )
    allowed_ids = selected_scenario_ids()
    helm_names = helm_repo_basenames()

    kept: list[pytest.Item] = []
    dropped = 0
    skipped_repo = 0
    for item in items:
        nodeid = item.nodeid.replace("\\", "/")
        path = str(getattr(item, "path", "") or "").replace("\\", "/")
        sid = scenario_id_from_nodeid(nodeid)
        if sid:
            target = SCENARIO_TARGET_REPO.get(sid, "")
            if target:
                item.add_marker(getattr(pytest.mark, marker_for_repo(target)))
                if target in helm_names:
                    item.add_marker(pytest.mark.helm_repo)
            if allowed_ids is not None and sid not in allowed_ids:
                item.add_marker(skip_repo)
                skipped_repo += 1
        is_e2e_live = (
            "test_scenarios_e2e.py" in nodeid
            and "e2e_preflight" in getattr(item, "fixturenames", [])
        )
        if collecting_folder and is_e2e_live:
            dropped += 1
            continue
        if collecting_folder and "tick_detects_live_issue" in nodeid:
            item.add_marker(skip_duplicate_01)
        if not e2e_enabled and not single and "/scenario_" in path:
            item.add_marker(skip_live)
        kept.append(item)
    items[:] = kept
    if dropped:
        _logger.info(
            "deselected %d duplicate live tests from test_scenarios_e2e.py "
            "(All scenarios uses scenario_XX.py)",
            dropped,
        )
    if skipped_repo:
        _logger.info(
            "skipped %d scenario tests via TEST_SCENARIO_REPOS=%s allowed=%s helm=%s",
            skipped_repo,
            os.environ.get("TEST_SCENARIO_REPOS"),
            sorted(allowed_ids or []),
            sorted(helm_names),
        )


def pytest_runtest_setup(item) -> None:
    run_metrics.set_current_test(item.nodeid)
    callspec = getattr(item, "callspec", None)
    params = getattr(callspec, "params", None) or {}
    mode = params.get("agent_mode")
    if mode:
        apply_agent_mode(str(mode))
    item._guardrails_env_prev = os.environ.get("GUARDRAILS_ENABLED")  # type: ignore[attr-defined]
    item._guardrails_env_touched = False  # type: ignore[attr-defined]
    if "guardrails_on" in params:
        item._guardrails_env_touched = True  # type: ignore[attr-defined]
        apply_guardrails_enabled(bool(params["guardrails_on"]))


def pytest_runtest_teardown(item) -> None:
    if getattr(item, "_guardrails_env_touched", False):
        prev = getattr(item, "_guardrails_env_prev", None)
        if prev is None:
            os.environ.pop("GUARDRAILS_ENABLED", None)
        else:
            os.environ["GUARDRAILS_ENABLED"] = prev
    run_metrics.clear_current_test()


_LIVE_FIXTURES = {
    "e2e_preflight",
    "live_github_issue",
    "live_scenario02_pr",
    "live_gateway_pom_pr",
    "live_scenario07_pr",
    "live_scenario03_create_issue",
}


def _live_agent_mode(item) -> str | None:
    callspec = getattr(item, "callspec", None)
    params = getattr(callspec, "params", None) or {}
    mode = params.get("agent_mode")
    return str(mode) if mode else None


def _is_live_benchmark(item) -> bool:
    fixtures = set(getattr(item, "fixturenames", []) or [])
    if not (fixtures & _LIVE_FIXTURES):
        return False
    nodeid = item.nodeid.replace("\\", "/")
    if "tick_detects_live_issue" in nodeid:
        return False
    return True


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not _is_live_benchmark(item):
        return
    nodeid = item.nodeid.replace("\\", "/")
    agent_mode = _live_agent_mode(item)
    if report.when == "setup" and report.skipped:
        reason = str(report.longrepr)[:500] if report.longrepr else "skipped"
        run_metrics.note_pytest_outcome(
            nodeid,
            passed=False,
            duration_s=report.duration,
            error=reason,
            skipped=True,
            live=True,
            agent_mode=agent_mode,
        )
        return
    if report.when == "setup" and not report.passed:
        error = str(report.longrepr)[:500] if report.longrepr else "setup failed"
        run_metrics.note_pytest_outcome(
            nodeid, False, report.duration, error, live=True, agent_mode=agent_mode
        )
        return
    if report.when != "call":
        return
    error = None
    if not report.passed:
        error = str(report.longrepr)[:500] if report.longrepr else "failed"
    run_metrics.note_pytest_outcome(
        nodeid, report.passed, report.duration, error, live=True, agent_mode=agent_mode
    )


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    if not run_metrics.has_live_rows():
        return
    e2e = _is_truthy(os.environ.get("RUN_SCENARIO_E2E")) or _running_single_scenario(session.config)
    if not e2e:
        return
    path = run_metrics.write_reports()
    if path:
        print(f"\n[scenarios] report written to {path}")
    else:
        print(f"\n[scenarios] report directory: {_REPORTS_DIR}")


def pytest_ignore_collect(collection_path, config) -> bool:  # noqa: ARG001
    path = Path(collection_path)
    if path.name.endswith(".old.py"):
        return True
    return False
