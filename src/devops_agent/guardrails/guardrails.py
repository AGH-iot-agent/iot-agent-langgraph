"""
guardrails.py – thin integration shim
======================================
Previously this file imported the third-party ``guardrails`` library which is
not a core dependency and is not guaranteed to be installed.

All threat-detection logic has been consolidated into
``devops_agent.security.SecurityLayer``.  This module now re-exports the
singleton and a convenience ``validate_llm_response`` helper so any existing
call-sites continue to work.
"""
from __future__ import annotations

from devops_agent.security import security_layer, SecurityScanResult  # noqa: F401


def validate_llm_response(text: str) -> str:
    """
    Scan an LLM response for secrets and PII, returning the sanitised text.
    Raises ``ValueError`` if a critical violation is found.

    This replaces the previous guardrails-library-based implementation.
    """
    result: SecurityScanResult = security_layer.scan_output(text)
    if result.has_severity("critical"):
        critical = [v for v in result.violations if v.severity == "critical"]
        raise ValueError(
            f"Critical security violation in LLM output: {critical[0].description}"
        )
    # Return sanitised text even for lower-severity violations
    return result.sanitized_text
