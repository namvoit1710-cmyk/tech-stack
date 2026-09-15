"""Unit tests for the worker's dynamic functions (app/functions.py).

These are async design-time/runtime helpers built on httpx.AsyncClient, so the
HTTP boundary is mocked with AsyncMock (the async collaborator idiom). No real
network — the client + its context manager + .request are all mocked.

Case coverage per function: happy · invalid-input (missing url) · failure-path
(exception) · edge/boundary (non-dict / empty-list JSON, header defaults).
"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Import the module (not the handlers by name): a module-level ``test_connection``
# binding would be collected by pytest as a bogus test. Reference via ``functions.``.
import app.functions as functions
from app.functions import HTTP_WORKER_FUNCTIONS


def _async_client_mock(response=None, request_side_effect=None):
    """Build a mock standing in for `async with httpx.AsyncClient() as c`.

    Returns the class-level mock to patch `httpx.AsyncClient` with. The instance
    supports the async context-manager protocol and an async `.request`.
    """
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if request_side_effect is not None:
        client.request = AsyncMock(side_effect=request_side_effect)
    else:
        client.request = AsyncMock(return_value=response)
    cls = MagicMock(return_value=client)
    return cls, client


def _resp(status_code=200, headers=None, json_data=None, elapsed_ms=12):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = headers or {"content-type": "application/json"}
    resp.elapsed = timedelta(milliseconds=elapsed_ms)
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


class TestConnection:
    @pytest.mark.asyncio
    async def test_reachable_happy_path(self):
        cls, client = _async_client_mock(
            _resp(200, headers={"content-type": "text/html"})
        )
        with patch("app.functions.httpx.AsyncClient", cls):
            result = await functions.test_connection(
                {"url": "https://example.com", "method": "get", "timeout_seconds": 5}
            )
        assert result["reachable"] is True
        assert result["status_code"] == 200
        assert result["content_type"] == "text/html"
        assert result["latency_ms"] == pytest.approx(12.0)
        # method upper-cased before dispatch
        assert client.request.await_args.args[0] == "GET"

    @pytest.mark.asyncio
    async def test_default_method_is_head(self):
        cls, client = _async_client_mock(_resp(200))
        with patch("app.functions.httpx.AsyncClient", cls):
            result = await functions.test_connection({"url": "https://example.com"})
        assert result["reachable"] is True
        assert client.request.await_args.args[0] == "HEAD"

    @pytest.mark.asyncio
    async def test_missing_url_invalid_input(self):
        # No network client should ever be built when url is missing.
        with patch("app.functions.httpx.AsyncClient") as cls:
            result = await functions.test_connection({"method": "GET"})
        assert result == {"reachable": False, "error": "url is required"}
        cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_connection_failure_path(self):
        cls, _ = _async_client_mock(
            request_side_effect=httpx.ConnectError("refused")
        )
        with patch("app.functions.httpx.AsyncClient", cls):
            result = await functions.test_connection({"url": "https://down.example.com"})
        assert result["reachable"] is False
        assert "refused" in result["error"]


class TestExtractResponseKeys:
    @pytest.mark.asyncio
    async def test_dict_response_returns_top_level_keys(self):
        resp = _resp(200, json_data={"a": 1, "b": 2})
        cls, client = _async_client_mock(resp)
        with patch("app.functions.httpx.AsyncClient", cls):
            result = await functions.extract_response_keys(
                {"url": "https://api.example.com", "method": "get"}
            )
        assert set(result["keys"]) == {"a", "b"}
        assert result["is_array"] is False
        assert result["sample_count"] == 1
        assert result["status_code"] == 200
        assert client.request.await_args.args[0] == "GET"

    @pytest.mark.asyncio
    async def test_array_response_uses_first_element_keys(self):
        resp = _resp(200, json_data=[{"x": 1}, {"x": 2}])
        cls, _ = _async_client_mock(resp)
        with patch("app.functions.httpx.AsyncClient", cls):
            result = await functions.extract_response_keys({"url": "https://api.example.com"})
        assert result["keys"] == ["x"]
        assert result["is_array"] is True
        assert result["sample_count"] == 2

    @pytest.mark.asyncio
    async def test_scalar_or_empty_response_yields_no_keys(self):
        # Boundary: a top-level list of non-dicts (or empty) -> keys == [].
        resp = _resp(200, json_data=[1, 2, 3])
        cls, _ = _async_client_mock(resp)
        with patch("app.functions.httpx.AsyncClient", cls):
            result = await functions.extract_response_keys({"url": "https://api.example.com"})
        assert result["keys"] == []
        assert result["is_array"] is True
        assert result["sample_count"] == 3

    @pytest.mark.asyncio
    async def test_missing_url_invalid_input(self):
        with patch("app.functions.httpx.AsyncClient") as cls:
            result = await functions.extract_response_keys({})
        assert result == {"keys": [], "error": "url is required"}
        cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_path_returns_error(self):
        cls, _ = _async_client_mock(request_side_effect=httpx.ReadTimeout("slow"))
        with patch("app.functions.httpx.AsyncClient", cls):
            result = await functions.extract_response_keys({"url": "https://api.example.com"})
        assert result["keys"] == []
        assert "slow" in result["error"]

    @pytest.mark.asyncio
    async def test_passes_headers_through(self):
        resp = _resp(200, json_data={"ok": True})
        cls, client = _async_client_mock(resp)
        with patch("app.functions.httpx.AsyncClient", cls):
            await functions.extract_response_keys(
                {"url": "https://api.example.com", "headers": {"Accept": "application/json"}}
            )
        assert client.request.await_args.kwargs["headers"] == {"Accept": "application/json"}


class TestWorkerFunctionRegistry:
    def test_two_functions_registered_with_handlers(self):
        names = {f.name for f in HTTP_WORKER_FUNCTIONS}
        assert names == {"test_connection", "extract_response_keys"}
        handlers = {f.name: f.handler for f in HTTP_WORKER_FUNCTIONS}
        assert handlers["test_connection"] is functions.test_connection
        assert handlers["extract_response_keys"] is functions.extract_response_keys
