"""
Tests for retry logic in AgentCallCoordinator._call_sub_agent().

Covers:
- Successful retry after transient timeout
- Successful retry after transient 502/503/504
- Successful retry after transient connection error
- Max retries exhausted (timeout)
- Max retries exhausted (5xx)
- Max retries exhausted (connection error)
- Non-retryable 4xx errors are not retried
- Non-retryable generic exceptions are not retried
- Retry disabled (max_retries=0)
- Backoff delay increases exponentially
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest
from agent_sdk.layer1_domain.entities.agent_capability import AgentCapability
from agent_sdk.layer4_frameworks.http.agent_call_coordinator import (
    AgentCallCoordinator,
)


def _make_call(**overrides) -> AgentCallRequest:
    defaults = dict(
        agent_id="a-1",
        agent_type="test-agent",
        input_payload={"message": "hello"},
        interrupt_id="i-1",
        thread_id="t-1",
    )
    defaults.update(overrides)
    return AgentCallRequest(**defaults)


def _make_coordinator(http_client, **overrides):
    resolver = MagicMock()
    resolver.resolve_endpoint = AsyncMock(return_value="http://agent:9000")
    defaults = dict(
        endpoint_resolver=resolver,
        resume_use_case=MagicMock(),
        http_client=http_client,
        max_retries=3,
        retry_backoff_seconds=0.01,  # fast for tests
    )
    defaults.update(overrides)
    return AgentCallCoordinator(**defaults)


def _ok_response():
    return MagicMock(
        status_code=200,
        json=lambda: {"result": "ok"},
        raise_for_status=lambda: None,
    )


def _error_response(status_code):
    resp = MagicMock(status_code=status_code)
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"{status_code}", request=MagicMock(), response=resp
    )
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# Successful retries (transient failure then success)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retries_on_timeout_then_succeeds():
    """Coordinator retries on timeout and returns success when subsequent attempt works."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[httpx.TimeoutException("read timed out"), _ok_response()]
    )
    coordinator = _make_coordinator(http_client)
    result = await coordinator._call_sub_agent(_make_call())
    assert result == {"result": "ok"}
    assert http_client.post.call_count == 2


@pytest.mark.asyncio
async def test_retries_on_503_then_succeeds():
    """Coordinator retries on HTTP 503 and returns success on next attempt."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[MagicMock(status_code=503), _ok_response()]
    )
    coordinator = _make_coordinator(http_client)
    result = await coordinator._call_sub_agent(_make_call())
    assert result == {"result": "ok"}
    assert http_client.post.call_count == 2


@pytest.mark.asyncio
async def test_retries_on_502_then_succeeds():
    """Coordinator retries on HTTP 502."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[MagicMock(status_code=502), _ok_response()]
    )
    coordinator = _make_coordinator(http_client)
    result = await coordinator._call_sub_agent(_make_call())
    assert result == {"result": "ok"}
    assert http_client.post.call_count == 2


@pytest.mark.asyncio
async def test_retries_on_504_then_succeeds():
    """Coordinator retries on HTTP 504."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[MagicMock(status_code=504), _ok_response()]
    )
    coordinator = _make_coordinator(http_client)
    result = await coordinator._call_sub_agent(_make_call())
    assert result == {"result": "ok"}
    assert http_client.post.call_count == 2


@pytest.mark.asyncio
async def test_retries_on_connect_error_then_succeeds():
    """Coordinator retries on connection error and succeeds on next attempt."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[httpx.ConnectError("Connection refused"), _ok_response()]
    )
    coordinator = _make_coordinator(http_client)
    result = await coordinator._call_sub_agent(_make_call())
    assert result == {"result": "ok"}
    assert http_client.post.call_count == 2


@pytest.mark.asyncio
async def test_retries_on_remote_protocol_error_then_succeeds():
    """Coordinator retries on RemoteProtocolError and succeeds on next attempt."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[httpx.RemoteProtocolError("peer closed"), _ok_response()]
    )
    coordinator = _make_coordinator(http_client)
    result = await coordinator._call_sub_agent(_make_call())
    assert result == {"result": "ok"}
    assert http_client.post.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# Max retries exhausted
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_retries_exhausted_timeout():
    """After max_retries+1 timeout failures, coordinator returns error."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(side_effect=httpx.TimeoutException("read timed out"))
    coordinator = _make_coordinator(http_client, max_retries=2)
    result = await coordinator._call_sub_agent(_make_call())
    assert result["success"] is False
    assert result["error"] == "Sub-agent request failed"
    assert http_client.post.call_count == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_max_retries_exhausted_503():
    """After max_retries+1 HTTP 503 failures, coordinator returns error."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(return_value=MagicMock(status_code=503))
    coordinator = _make_coordinator(http_client, max_retries=2)
    result = await coordinator._call_sub_agent(_make_call())
    assert result["success"] is False
    assert "503" in result["error"]
    assert "3 attempts" in result["error"]
    assert http_client.post.call_count == 3


@pytest.mark.asyncio
async def test_max_retries_exhausted_connect_error():
    """After max_retries+1 connection errors, coordinator returns error."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
    coordinator = _make_coordinator(http_client, max_retries=2)
    result = await coordinator._call_sub_agent(_make_call())
    assert result["success"] is False
    assert result["error"] == "Sub-agent request failed"
    assert http_client.post.call_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# Non-retryable errors (must fail immediately)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_retry_on_400():
    """HTTP 400 is not retryable — coordinator returns error immediately."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(return_value=_error_response(400))
    coordinator = _make_coordinator(http_client)
    result = await coordinator._call_sub_agent(_make_call())
    assert result["success"] is False
    assert http_client.post.call_count == 1  # no retry


@pytest.mark.asyncio
async def test_no_retry_on_404():
    """HTTP 404 is not retryable — coordinator returns error immediately."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(return_value=_error_response(404))
    coordinator = _make_coordinator(http_client)
    result = await coordinator._call_sub_agent(_make_call())
    assert result["success"] is False
    assert http_client.post.call_count == 1


@pytest.mark.asyncio
async def test_no_retry_on_422():
    """HTTP 422 is not retryable — coordinator returns error immediately."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(return_value=_error_response(422))
    coordinator = _make_coordinator(http_client)
    result = await coordinator._call_sub_agent(_make_call())
    assert result["success"] is False
    assert http_client.post.call_count == 1


@pytest.mark.asyncio
async def test_no_retry_on_generic_exception():
    """Generic exceptions (e.g. ValueError) are not retried."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(side_effect=ValueError("bad data"))
    coordinator = _make_coordinator(http_client)
    result = await coordinator._call_sub_agent(_make_call())
    assert result["success"] is False
    assert result["error"] == "Sub-agent request failed"
    assert http_client.post.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# Retry disabled
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_retry_when_max_retries_zero():
    """With max_retries=0, coordinator does not retry — single attempt only."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    coordinator = _make_coordinator(http_client, max_retries=0)
    result = await coordinator._call_sub_agent(_make_call())
    assert result["success"] is False
    assert result["error"] == "Sub-agent request failed"
    assert http_client.post.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# Backoff behavior
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backoff_delays_increase_exponentially():
    """Backoff delays must follow 2^attempt pattern."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[
            httpx.TimeoutException("t1"),
            httpx.TimeoutException("t2"),
            httpx.TimeoutException("t3"),
            _ok_response(),
        ]
    )
    coordinator = _make_coordinator(
        http_client, max_retries=3, retry_backoff_seconds=1.0
    )

    delays = []

    async def mock_sleep(delay):
        delays.append(delay)

    with patch("asyncio.sleep", side_effect=mock_sleep):
        result = await coordinator._call_sub_agent(_make_call())

    assert result == {"result": "ok"}
    assert delays == [1.0, 2.0, 4.0]  # 1*2^0, 1*2^1, 1*2^2


@pytest.mark.asyncio
async def test_backoff_uses_custom_base():
    """Backoff base is configurable via retry_backoff_seconds."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[httpx.TimeoutException("t1"), _ok_response()]
    )
    coordinator = _make_coordinator(
        http_client, max_retries=3, retry_backoff_seconds=0.5
    )

    delays = []

    async def mock_sleep(delay):
        delays.append(delay)

    with patch("asyncio.sleep", side_effect=mock_sleep):
        await coordinator._call_sub_agent(_make_call())

    assert delays == [0.5]  # 0.5 * 2^0


# ─────────────────────────────────────────────────────────────────────────────
# Mixed transient failures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mixed_transient_failures_then_success():
    """Coordinator retries across different transient failure types."""
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[
            httpx.TimeoutException("timeout"),
            httpx.ConnectError("refused"),
            MagicMock(status_code=503),
            _ok_response(),
        ]
    )
    coordinator = _make_coordinator(http_client, max_retries=3)
    result = await coordinator._call_sub_agent(_make_call())
    assert result == {"result": "ok"}
    assert http_client.post.call_count == 4


# ─────────────────────────────────────────────────────────────────────────────
# Existing tests still work (required_parameters validation skips retry)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_required_params_validation_skips_retry():
    """Missing required params returns error immediately without any HTTP call."""
    cap = AgentCapability(
        agent_type="test-agent",
        name="Test",
        description="test",
        input_schema={},
        output_schema={},
        required_parameters=["file_id"],
    )
    http_client = AsyncMock()
    coordinator = _make_coordinator(http_client, capabilities={"test-agent": cap})
    call = _make_call(input_payload={"message": "hello"})  # missing file_id
    result = await coordinator._call_sub_agent(call)
    assert result["success"] is False
    assert "file_id" in result["error"]
    http_client.post.assert_not_called()
