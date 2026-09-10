"""Reusable tenacity retry helpers for httpx-based infrastructure clients."""

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_fixed,
)

class HttpxRetryHelper:
    """Helper class for retrying HTTPX requests with tenacity."""

    @staticmethod
    def should_retry_httpx(retryable_status_codes: list[int], exception: Exception) -> bool:
        """Return True if the exception warrants a retry.

        Retries on retryable HTTP status codes (e.g. 429, 5xx) and transient
        network errors (timeout, connection, read, protocol errors).
        """
        if isinstance(exception, httpx.HTTPStatusError):
            return exception.response.status_code in retryable_status_codes
        return isinstance(exception, (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.RemoteProtocolError,
        ))

    @staticmethod
    def wrap_with_exponential_retry(
        func,
        *,
        retry_count: int,
        multiplier: int,
        min_wait: int,
        max_wait: int,
        retry_predicate,
    ):
        """Wrap an async function with exponential-backoff retry via tenacity."""
        return retry(
            stop=stop_after_attempt(retry_count),
            wait=wait_exponential(multiplier=multiplier, min=min_wait, max=max_wait),
            retry=retry_if_exception(retry_predicate),
            reraise=True,
        )(func)

    @staticmethod
    def wrap_with_fixed_retry(
        func,
        *,
        retry_count: int,
        wait_seconds: int,
        retry_predicate,
    ):
        """Wrap an async function with fixed-wait retry via tenacity."""
        return retry(
            stop=stop_after_attempt(retry_count),
            wait=wait_fixed(wait_seconds),
            retry=retry_if_exception(retry_predicate),
            reraise=True,
        )(func)