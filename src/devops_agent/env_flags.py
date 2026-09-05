"""Process-level feature flags. Default-on flags treat missing/empty as enabled."""
from __future__ import annotations

import os

_FALSE = frozenset({"0", "false", "no", "off"})


def env_flag_enabled(name: str, *, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() not in _FALSE


def guardrails_enabled() -> bool:
    """``GUARDRAILS_ENABLED`` (default true). ``0``/``false``/``no``/``off`` disable input/policy gates."""
    return env_flag_enabled("GUARDRAILS_ENABLED", default=True)
