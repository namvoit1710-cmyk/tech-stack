"""Unit tests for the Smartsheet events HTTP client (smartsheet-event-trigger S2).

Hermetic via ``httpx.MockTransport`` — verifies request shaping (maxCount + cursor,
Bearer auth), response parsing, and error mapping without a live token/network.
"""

import httpx
import pytest

from app.layer4_frameworks.clients.smartsheet_events_client import (
    SmartsheetApiError,
    SmartsheetAuthError,
    SmartsheetEventsClient,
    SmartsheetRateLimitError,
)

pytestmark = pytest.mark.unit


def _client(handler, token="tok-123", **kw):
    transport = httpx.MockTransport(handler)
    return SmartsheetEventsClient(token, client=httpx.Client(transport=transport), **kw)


def test_fetch_page_shapes_request_and_parses_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["maxCount"] = request.url.params.get("maxCount")
        seen["since"] = request.url.params.get("since")
        return httpx.Response(200, json={
            "data": [{"objectType": "row", "action": "updated"}],
            "nextStreamPosition": "pos-2",
            "moreAvailable": True,
        })

    client = _client(handler, max_count=500)
    page = client.fetch_page({"since": "2026-07-18T00:00:00Z"})

    assert seen["auth"] == "Bearer tok-123"
    assert seen["maxCount"] == "500"
    assert seen["since"] == "2026-07-18T00:00:00Z"
    assert "/2.0/events" in seen["url"]
    assert page.events == [{"objectType": "row", "action": "updated"}]
    assert page.next_stream_position == "pos-2"
    assert page.more_available is True


def test_stream_position_passed_through():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["streamPosition"] = request.url.params.get("streamPosition")
        return httpx.Response(200, json={"data": [], "moreAvailable": False})

    _client(handler).fetch_page({"streamPosition": "pos-9"})
    assert seen["streamPosition"] == "pos-9"


def test_missing_fields_default_safely():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})  # no data / nextStreamPosition / moreAvailable

    page = _client(handler).fetch_page({"since": "t"})
    assert page.events == []
    assert page.next_stream_position == ""
    assert page.more_available is False


def test_403_maps_to_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errorCode": 1013, "message": "not supported by your plan"})

    with pytest.raises(SmartsheetAuthError):
        _client(handler).fetch_page({"since": "t"})


def test_429_maps_to_rate_limit_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "rate limited"})

    with pytest.raises(SmartsheetRateLimitError):
        _client(handler).fetch_page({"since": "t"})


def test_500_maps_to_generic_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(SmartsheetApiError):
        _client(handler).fetch_page({"since": "t"})
