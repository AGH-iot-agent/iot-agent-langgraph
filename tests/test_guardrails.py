"""
Unit tests for the Guardrails AI integration layer.

These tests cover:
  1. run_output_guard() returns sanitised text + violations for known patterns
  2. validate_llm_response() raises ValueError on critical violations
  3. validate_llm_response() returns sanitised text for lower-severity hits
  4. Clean input → is_safe=True, no violations
  5. Fallback to regex SecurityLayer when Guard unavailable (ImportError)

The tests are written so they pass even when ``guardrails-ai`` Hub validators
are not installed: the fallback path (regex SecurityLayer) covers all threat
classes independently.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_OPENAI_KEY = "sk-" + "A" * 48                  # OpenAI token  → critical
_FAKE_AWS_KEY    = "AKIA" + "B" * 16                  # AWS AKID       → critical
_FAKE_GHS_TOKEN  = "ghs_" + "C" * 36                  # GitHub app token → high (not critical)
_FAKE_API_KEY    = "api_key=" + "D" * 24              # generic API key  → high (not critical)
_FAKE_EMAIL      = "john.doe@example.com"
_CLEAN_TEXT      = "The deployment rolled out successfully across all 3 replicas."


# ---------------------------------------------------------------------------
# Tests: run_output_guard – output sanitisation
# ---------------------------------------------------------------------------

class TestRunOutputGuard:
    """run_output_guard() should sanitise LLM output regardless of backend."""

    def _get_guard(self):
        """Fresh import of run_output_guard (after any monkeypatching)."""
        import devops_agent.guardrails.guardrails as m
        return m.run_output_guard

    def test_clean_text_is_safe(self):
        from devops_agent.guardrails.guardrails import run_output_guard
        result = run_output_guard(_CLEAN_TEXT)
        assert result.is_safe
        assert result.violations == []
        assert result.sanitized_text == _CLEAN_TEXT

    def test_openai_key_detected(self):
        from devops_agent.guardrails.guardrails import run_output_guard
        text = f"Here is the key: {_FAKE_OPENAI_KEY}"
        result = run_output_guard(text)
        # Violation must be detected (either via Guard or regex fallback)
        assert not result.is_safe
        threat_types = [v.threat_type for v in result.violations]
        assert "secret_leakage" in threat_types

    def test_openai_key_redacted_in_output(self):
        from devops_agent.guardrails.guardrails import run_output_guard
        text = f"Your token is {_FAKE_OPENAI_KEY} – keep it safe."
        result = run_output_guard(text)
        assert _FAKE_OPENAI_KEY not in result.sanitized_text

    def test_aws_key_detected(self):
        from devops_agent.guardrails.guardrails import run_output_guard
        text = f"AWS key: {_FAKE_AWS_KEY}"
        result = run_output_guard(text)
        assert not result.is_safe
        assert any(v.threat_type == "secret_leakage" for v in result.violations)

    def test_email_detected(self):
        from devops_agent.guardrails.guardrails import run_output_guard
        text = f"Contact {_FAKE_EMAIL} for access."
        result = run_output_guard(text)
        # PII detected (either via Guard Presidio or regex fallback)
        threat_types = [v.threat_type for v in result.violations]
        assert "pii_leakage" in threat_types or "email" in str(threat_types)

    def test_pem_key_is_critical(self):
        from devops_agent.guardrails.guardrails import run_output_guard
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
        result = run_output_guard(text)
        assert not result.is_safe
        severities = [v.severity for v in result.violations]
        assert "critical" in severities


# ---------------------------------------------------------------------------
# Tests: validate_llm_response – public API
# ---------------------------------------------------------------------------

class TestValidateLlmResponse:
    """validate_llm_response() must raise on critical or return sanitised text."""

    def test_clean_text_passthrough(self):
        from devops_agent.guardrails.guardrails import validate_llm_response
        result = validate_llm_response(_CLEAN_TEXT)
        assert result == _CLEAN_TEXT

    def test_critical_violation_raises(self):
        from devops_agent.guardrails.guardrails import validate_llm_response
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
        with pytest.raises(ValueError, match="Critical security violation"):
            validate_llm_response(pem)

    def test_high_violation_returns_sanitised(self):
        # Uses a high-severity (not critical) secret so validate_llm_response
        # redacts and returns rather than raising ValueError.
        from devops_agent.guardrails.guardrails import validate_llm_response
        text = f"Use token: {_FAKE_GHS_TOKEN} to authenticate."
        result = validate_llm_response(text)
        assert _FAKE_GHS_TOKEN not in result

    def test_multiple_secrets_all_redacted(self):
        # Use run_output_guard directly to verify redaction regardless of severity;
        # critical secrets legitimately raise in validate_llm_response.
        from devops_agent.guardrails.guardrails import run_output_guard
        text = (
            f"API key: {_FAKE_OPENAI_KEY}\n"
            f"AWS: {_FAKE_AWS_KEY}\n"
            f"Contact: {_FAKE_EMAIL}\n"
        )
        result = run_output_guard(text)
        assert _FAKE_OPENAI_KEY not in result.sanitized_text
        assert _FAKE_AWS_KEY not in result.sanitized_text


# ---------------------------------------------------------------------------
# Tests: fallback behaviour when guardrails-ai is not installed
# ---------------------------------------------------------------------------

class TestFallbackToRegex:
    """When Guard is unavailable, the regex SecurityLayer must take over."""

    def test_fallback_still_detects_secrets(self):
        """Simulate missing guardrails Hub by temporarily disabling the guard."""
        import devops_agent.guardrails.guardrails as mod

        original_guard = mod._output_guard
        original_available = mod._GUARDRAILS_AVAILABLE
        try:
            mod._output_guard = None
            mod._GUARDRAILS_AVAILABLE = False

            from devops_agent.guardrails.guardrails import run_output_guard
            text = f"token: {_FAKE_OPENAI_KEY}"
            result = run_output_guard(text)

            assert not result.is_safe
            assert any(v.threat_type == "secret_leakage" for v in result.violations)
        finally:
            mod._output_guard = original_guard
            mod._GUARDRAILS_AVAILABLE = original_available

    def test_fallback_clean_text_still_passes(self):
        import devops_agent.guardrails.guardrails as mod

        original_guard = mod._output_guard
        original_available = mod._GUARDRAILS_AVAILABLE
        try:
            mod._output_guard = None
            mod._GUARDRAILS_AVAILABLE = False

            from devops_agent.guardrails.guardrails import run_output_guard
            result = run_output_guard(_CLEAN_TEXT)
            assert result.is_safe
        finally:
            mod._output_guard = original_guard
            mod._GUARDRAILS_AVAILABLE = original_available

    def test_guard_runtime_exception_falls_back(self):
        """If Guard.validate() raises, we fall back and do NOT propagate the error."""
        import devops_agent.guardrails.guardrails as mod

        broken_guard = MagicMock()
        broken_guard.validate.side_effect = RuntimeError("spacy model missing")

        original_guard = mod._output_guard
        original_available = mod._GUARDRAILS_AVAILABLE
        try:
            mod._output_guard = broken_guard
            mod._GUARDRAILS_AVAILABLE = True

            from devops_agent.guardrails.guardrails import run_output_guard
            # Should NOT raise – fallback to regex
            result = run_output_guard(_CLEAN_TEXT)
            assert isinstance(result.is_safe, bool)
        finally:
            mod._output_guard = original_guard
            mod._GUARDRAILS_AVAILABLE = original_available


# ---------------------------------------------------------------------------
# Tests: security_nodes integration
# ---------------------------------------------------------------------------

class TestSecurityOutputNode:
    """security_output_node() should apply both regex + Guard passes."""

    def _make_state(self, **kwargs):
        base = {
            "request": "test",
            "final_summary": "",
            "execution_summary": "",
            "security_violations": [],
            "security_blocked": False,
        }
        base.update(kwargs)
        return base

    def test_node_sanitises_final_summary(self):
        from devops_agent.nodes.security_nodes import security_output_node
        state = self._make_state(
            final_summary=f"Deployment key: {_FAKE_OPENAI_KEY} applied."
        )
        result = security_output_node(state)
        assert _FAKE_OPENAI_KEY not in result["final_summary"]
        assert len(result["security_violations"]) > 0

    def test_node_clean_summary_unchanged(self):
        from devops_agent.nodes.security_nodes import security_output_node
        state = self._make_state(final_summary=_CLEAN_TEXT)
        result = security_output_node(state)
        assert result["final_summary"] == _CLEAN_TEXT
        assert result["security_violations"] == []

    def test_node_sanitises_execution_summary(self):
        from devops_agent.nodes.security_nodes import security_output_node
        state = self._make_state(
            execution_summary=f"AWS access key {_FAKE_AWS_KEY} was used."
        )
        result = security_output_node(state)
        assert _FAKE_AWS_KEY not in result["execution_summary"]
