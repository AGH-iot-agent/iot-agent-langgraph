"""Select AGENT_MODE variants for scenario 01–20.

Default (thesis): both ``multi_agent`` and ``single_agent`` in one pytest
invocation. Debug one topology with::

    SCENARIO_AGENT_MODES=multi_agent
    SCENARIO_AGENT_MODES=single_agent
"""
from __future__ import annotations

import os

_VALID = ("multi_agent", "single_agent")
_DEFAULT = "multi_agent,single_agent"


def scenario_agent_modes() -> list[str]:
    raw = os.environ.get("SCENARIO_AGENT_MODES", _DEFAULT)
    modes: list[str] = []
    seen: set[str] = set()
    for part in str(raw or "").replace(";", ",").split(","):
        mode = part.strip().lower()
        if mode not in _VALID or mode in seen:
            continue
        seen.add(mode)
        modes.append(mode)
    return modes or ["multi_agent"]


def apply_agent_mode(mode: str) -> str:
    """Set AGENT_MODE before ``build_graph()`` so topology matches the test."""
    normalized = mode.strip().lower()
    if normalized not in _VALID:
        normalized = "multi_agent"
    os.environ["AGENT_MODE"] = normalized
    return normalized


def apply_guardrails_enabled(enabled: bool) -> None:
    """Set ``GUARDRAILS_ENABLED`` for a live 15/16 ablation trial."""
    os.environ["GUARDRAILS_ENABLED"] = "1" if enabled else "0"
