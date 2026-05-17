from __future__ import annotations

import logging
import re
import time

logger = logging.getLogger(__name__)

_RETRY_AFTER_RE = re.compile(r"try again in ([\d.]+)s", re.IGNORECASE)

_MIN_DELAY = 1.0   # never wait less than 1 s
_MAX_DELAY = 60.0  # cap at 60 s
_BUFFER    = 0.5   # add 500 ms on top of the API-suggested delay


def _parse_retry_after(exc: Exception) -> float | None:
    """Extract the suggested retry delay (seconds) from an OpenAI 429 error message."""
    m = _RETRY_AFTER_RE.search(str(exc))
    if m:
        return float(m.group(1))
    return None


def invoke_with_retry(llm, messages, max_retries: int = 6, logger_name: str = "llm_utils"):
    """Invoke *llm* with smart retry on 429 RateLimitError.

    Delay strategy (in order of preference):
    1. Parse ``retry-after`` from the error body (e.g. "Please try again in 1.2s")
       and wait that duration + BUFFER (0.5 s).
    2. Fall back to doubling backoff starting at MIN_DELAY, capped at MAX_DELAY.
    """
    from openai import RateLimitError

    node_logger = logging.getLogger(logger_name)
    fallback_delay = _MIN_DELAY

    for attempt in range(max_retries):
        try:
            return llm.invoke(messages)
        except RateLimitError as exc:
            if attempt == max_retries - 1:
                raise
            suggested = _parse_retry_after(exc)
            if suggested is not None:
                delay = min(suggested + _BUFFER, _MAX_DELAY)
            else:
                delay = fallback_delay
                fallback_delay = min(fallback_delay * 2, _MAX_DELAY)
            node_logger.warning(
                "[LLM] Rate limit hit (attempt %d/%d), retrying in %.2fs: %s",
                attempt + 1, max_retries, delay, exc,
            )
            time.sleep(delay)

    return llm.invoke(messages)
