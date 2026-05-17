from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def pytest_sessionstart(session) -> None:
    """Load project-level .env for all test runs, regardless of runner entrypoint."""
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")
