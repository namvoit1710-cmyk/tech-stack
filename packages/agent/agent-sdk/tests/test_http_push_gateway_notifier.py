"""Unit tests for HttpPushGatewayNotifier (SDK)."""

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agent_sdk.layer4_frameworks.messaging.http_push_gateway_notifier import (
    HttpPushGatewayNotifier,
)


@pytest.mark.asyncio
async def test_post_body_structure():
    """POST body contains key, data as JSON string, and is_final."""
    notifier = HttpPushGatewayNotifier("http://localhost:8000")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    notifier._client = mock_client

    await notifier.send_notification(
        key="conv-1",
        data={"type": "response", "content": "hi"},
    )

    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args[0][0] == "http://localhost:8000/v1/pushgateway/send"

    payload = call_args[1]["json"]
    assert payload["key"] == "conv-1"
    assert payload["is_final"] is False
    assert isinstance(payload["data"], str)
    parsed = json.loads(payload["data"])
    assert parsed == {"type": "response", "content": "hi"}


@pytest.mark.asyncio
async def test_dict_serialization_to_json_string():
    """Dict data payloads are JSON-serialized to strings."""
    notifier = HttpPushGatewayNotifier("http://localhost:8000")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    notifier._client = mock_client

    data = {"nested": {"key": "value"}, "list": [1, 2, 3]}
    await notifier.send_notification(key="conv-2", data=data)

    payload = mock_client.post.call_args[1]["json"]
    assert json.loads(payload["data"]) == data


@pytest.mark.asyncio
async def test_is_final_false():
    """is_final=False is correctly passed in the payload."""
    notifier = HttpPushGatewayNotifier("http://localhost:8000")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    notifier._client = mock_client

    await notifier.send_notification(key="conv-4", data={}, is_final=False)

    payload = mock_client.post.call_args[1]["json"]
    assert payload["is_final"] is False


@pytest.mark.asyncio
async def test_http_error_logged_no_raise():
    """HTTP errors are logged as warnings, never raised."""
    notifier = HttpPushGatewayNotifier("http://localhost:8000")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        ),
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    notifier._client = mock_client

    await notifier.send_notification(key="conv-5", data={"type": "test"})


@pytest.mark.asyncio
async def test_close_shuts_down_client():
    """close() shuts down the httpx client."""
    notifier = HttpPushGatewayNotifier("http://localhost:8000")

    mock_client = AsyncMock()
    mock_client.aclose = AsyncMock()
    notifier._client = mock_client

    await notifier.close()

    mock_client.aclose.assert_called_once()
    assert notifier._client is None


@pytest.mark.asyncio
async def test_trailing_slash_stripped():
    """Trailing slash on base URL is stripped."""
    notifier = HttpPushGatewayNotifier("http://localhost:8000/")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    notifier._client = mock_client

    await notifier.send_notification(key="conv-9", data={})

    url = mock_client.post.call_args[0][0]
    assert url == "http://localhost:8000/v1/pushgateway/send"
