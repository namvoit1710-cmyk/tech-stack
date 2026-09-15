"""Retry policy with exponential backoff and error classification."""

from __future__ import annotations

from dataclasses import dataclass

# Errors that should never be retried
NON_RETRYABLE_ERRORS = frozenset({
    "VALIDATION_ERROR",
    "EXPRESSION_ERROR",
    "PERMISSION_DENIED",
})

# Errors that are safe to retry
RETRYABLE_ERRORS = frozenset({
    "WORKER_TIMEOUT",
    "WORKER_ERROR",
    "INFRASTRUCTURE_ERROR",
})


@dataclass
class RetryDecision:
    should_retry: bool
    delay_seconds: float = 0.0
    next_attempt: int = 0


class RetryPolicy:

    def __init__(
        self,
        max_retries: int = 3,
        base_delay_ms: int = 1000,
        backoff: str = "exponential",
    ) -> None:
        self._max_retries = max_retries
        self._base_delay_ms = base_delay_ms
        self._backoff = backoff

    def evaluate(self, attempt: int, error_code: str) -> RetryDecision:
        """Decide whether to retry given the current attempt and error code."""
        if error_code in NON_RETRYABLE_ERRORS:
            return RetryDecision(should_retry=False)

        if attempt >= self._max_retries:
            return RetryDecision(should_retry=False)

        delay = self._compute_delay(attempt)
        return RetryDecision(
            should_retry=True,
            delay_seconds=delay,
            next_attempt=attempt + 1,
        )

    def _compute_delay(self, attempt: int) -> float:
        if self._backoff == "exponential":
            return (self._base_delay_ms * (2 ** attempt)) / 1000.0
        return self._base_delay_ms / 1000.0
