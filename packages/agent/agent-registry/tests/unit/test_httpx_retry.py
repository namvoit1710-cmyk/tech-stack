"""Unit tests for httpx_retry helper functions."""

import httpx
import pytest
from unittest.mock import Mock

from app.layer4_infrastructure.external_tools.httpx_retry import HttpxRetryHelper

RETRYABLE_CODES = [429, 500, 502, 503, 504]


class TestShouldRetryHttpx:
    def test_returns_true_for_retryable_status_codes(self):
        for code in RETRYABLE_CODES:
            exc = httpx.HTTPStatusError(str(code), request=Mock(), response=Mock(status_code=code))
            assert HttpxRetryHelper.should_retry_httpx(RETRYABLE_CODES, exc) is True, f"Expected retry for status {code}"

    def test_returns_false_for_non_retryable_status_code(self):
        exc = httpx.HTTPStatusError("404 Not Found", request=Mock(), response=Mock(status_code=404))
        assert HttpxRetryHelper.should_retry_httpx(RETRYABLE_CODES, exc) is False

    @pytest.mark.parametrize("exc_type", [
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.ReadError,
        httpx.RemoteProtocolError,
    ])
    def test_returns_true_for_transient_network_errors(self, exc_type):
        exc = exc_type("error")
        assert HttpxRetryHelper.should_retry_httpx(RETRYABLE_CODES, exc) is True

    def test_returns_false_for_unrelated_exception(self):
        assert HttpxRetryHelper.should_retry_httpx(RETRYABLE_CODES, ValueError("nope")) is False


class TestWrapWithExponentialRetry:
    @pytest.mark.asyncio
    async def test_retries_until_success(self):
        attempts = 0

        async def flaky():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise httpx.ConnectError("fail")
            return "ok"

        wrapped = HttpxRetryHelper.wrap_with_exponential_retry(
            flaky,
            retry_count=3,
            multiplier=0,
            min_wait=0,
            max_wait=0,
            retry_predicate=lambda e: isinstance(e, httpx.ConnectError),
        )
        result = await wrapped()
        assert result == "ok"
        assert attempts == 3

    @pytest.mark.asyncio
    async def test_reraises_after_exhausted(self):
        async def always_fail():
            raise httpx.ConnectError("fail")

        wrapped = HttpxRetryHelper.wrap_with_exponential_retry(
            always_fail,
            retry_count=2,
            multiplier=0,
            min_wait=0,
            max_wait=0,
            retry_predicate=lambda e: isinstance(e, httpx.ConnectError),
        )
        with pytest.raises(httpx.ConnectError):
            await wrapped()

    @pytest.mark.asyncio
    async def test_does_not_retry_when_predicate_false(self):
        attempts = 0

        async def fail_once():
            nonlocal attempts
            attempts += 1
            raise ValueError("not retryable")

        wrapped = HttpxRetryHelper.wrap_with_exponential_retry(
            fail_once,
            retry_count=3,
            multiplier=0,
            min_wait=0,
            max_wait=0,
            retry_predicate=lambda e: False,
        )
        with pytest.raises(ValueError):
            await wrapped()
        assert attempts == 1


class TestWrapWithFixedRetry:
    @pytest.mark.asyncio
    async def test_retries_until_success(self):
        attempts = 0

        async def flaky(endpoint: str, timeout_seconds: int = 5) -> bool:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise httpx.ConnectError("fail")
            return True

        wrapped = HttpxRetryHelper.wrap_with_fixed_retry(
            flaky,
            retry_count=3,
            wait_seconds=0,
            retry_predicate=lambda e: isinstance(e, httpx.ConnectError),
        )
        result = await wrapped("http://localhost/health", 5)
        assert result is True
        assert attempts == 3

    @pytest.mark.asyncio
    async def test_reraises_after_exhausted(self):
        async def always_fail(endpoint: str, timeout_seconds: int = 5):
            raise httpx.TimeoutException("timeout")

        wrapped = HttpxRetryHelper.wrap_with_fixed_retry(
            always_fail,
            retry_count=2,
            wait_seconds=0,
            retry_predicate=lambda e: isinstance(e, httpx.TimeoutException),
        )
        with pytest.raises(httpx.TimeoutException):
            await wrapped("http://localhost/health", 5)

    @pytest.mark.asyncio
    async def test_does_not_retry_non_matching_exception(self):
        attempts = 0

        async def fail():
            nonlocal attempts
            attempts += 1
            raise ValueError("not retryable")

        wrapped = HttpxRetryHelper.wrap_with_fixed_retry(
            fail,
            retry_count=3,
            wait_seconds=0,
            retry_predicate=lambda e: isinstance(e, httpx.ConnectError),
        )
        with pytest.raises(ValueError):
            await wrapped()
        assert attempts == 1
