"""
guardrails.py – Guardrails AI integration
==========================================
Wraps LLM *output* validation using the ``guardrails-ai`` library with two
lightweight Hub validators:

  • ``DetectPII``      – Presidio-based PII detection and redaction
  • ``SecretsPresent`` – detect-secrets-based secret detection

Both validators are configured with ``on_fail="fix"`` which instructs the
Guard to redact the offending content rather than raise an exception, giving
us a sanitised string we can return to the caller.

When the ``guardrails`` library or its Hub validators are unavailable (e.g. in
a fresh dev environment where ``guardrails hub install`` hasn't been run yet),
all calls fall back transparently to the regex-based
``devops_agent.security.SecurityLayer``.

Public API
----------
    run_output_guard(text) -> SecurityScanResult
        Deep-scan *text* for PII and secrets; returns a ``SecurityScanResult``
        consistent with the rest of the security layer.

    validate_llm_response(text) -> str
        Convenience wrapper: returns sanitised text or raises ``ValueError`` on
        a critical violation.  Existing call-sites continue to work unchanged.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from devops_agent.security import SecurityScanResult, SecurityViolation, security_layer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guardrails AI – lazy initialisation with graceful fallback
# ---------------------------------------------------------------------------

_output_guard = None          # Guard | None
_GUARDRAILS_AVAILABLE = False  # becomes True when hub validators load


def _init_guard() -> None:
    """
    Attempt to build the output Guard with Hub validators.
    Called once at module load time; failures are non-fatal.
    """
    global _output_guard, _GUARDRAILS_AVAILABLE
    try:
        from guardrails import Guard                       # noqa: PLC0415
        from guardrails.hub import DetectPII, SecretsPresent  # noqa: PLC0415

        _output_guard = Guard().use_many(
            DetectPII(on_fail="fix"),
            SecretsPresent(on_fail="fix"),
        )
        _GUARDRAILS_AVAILABLE = True
        logger.info("[guardrails] Output guard initialised (DetectPII + SecretsPresent)")
    except ImportError as exc:
        logger.warning(
            "[guardrails] Hub validators not installed (%s). "
            "Falling back to regex SecurityLayer. "
            "Run: guardrails hub install hub://guardrails/detect_pii && "
            "guardrails hub install hub://guardrails/secrets_present",
            exc,
        )
    except Exception as exc:  # e.g. spacy model missing
        logger.warning("[guardrails] Guard initialisation failed (%s). Falling back to regex.", exc)


_init_guard()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def run_output_guard(text: str) -> SecurityScanResult:
    """
    Run the Guardrails AI output guard on *text*.

    Returns a ``SecurityScanResult`` with:
    - ``is_safe``        – True when no violations found
    - ``violations``     – list of detected threats
    - ``sanitized_text`` – redacted version of *text*

    Falls back to ``security_layer.scan_output()`` when the Guard is not
    available or raises an unexpected error.
    """
    if not _GUARDRAILS_AVAILABLE or _output_guard is None:
        return security_layer.scan_output(text)

    try:
        outcome = _output_guard.validate(text)
        sanitized: str = outcome.validated_output or text

        violations: list[SecurityViolation] = []

        # Map Guard-level validation summary to our SecurityViolation schema
        for detail in (outcome.validation_summaries or []):
            failed = getattr(detail, "validation_passed", True) is False
            if not failed:
                continue
            validator_name: str = getattr(detail, "validator_name", "unknown")
            # Map validator names to our threat taxonomy
            threat = (
                "pii_leakage"
                if "pii" in validator_name.lower() or "detect_pii" in validator_name.lower()
                else "secret_leakage"
            )
            violations.append(
                SecurityViolation(
                    threat_type=threat,
                    severity="high",
                    description=f"Guardrails AI [{validator_name}]: content redacted",
                    matched_pattern="guardrails-hub",
                    field="llm_output",
                )
            )

        is_safe = len(violations) == 0
        if not is_safe:
            logger.warning(
                "[guardrails] Output violations detected: %s",
                [v.threat_type for v in violations],
            )

        return SecurityScanResult(
            is_safe=is_safe,
            violations=violations,
            sanitized_text=sanitized,
        )

    except Exception as exc:
        logger.warning(
            "[guardrails] Guard.validate() failed (%s). Falling back to regex scan.", exc
        )
        return security_layer.scan_output(text)


def validate_llm_response(text: str) -> str:
    """
    Scan an LLM response for secrets and PII, returning the sanitised text.
    Raises ``ValueError`` if a critical violation is found.

    Uses the Guardrails AI output guard when available; falls back to the
    regex-based SecurityLayer otherwise.
    """
    result: SecurityScanResult = run_output_guard(text)
    if result.has_severity("critical"):
        critical = [v for v in result.violations if v.severity == "critical"]
        raise ValueError(
            f"Critical security violation in LLM output: {critical[0].description}"
        )
    return result.sanitized_text

