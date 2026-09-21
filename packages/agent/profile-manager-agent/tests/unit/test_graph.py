"""Unit specs for UserQueryRepository._execute_single (layer4, async httpx).

Target: app/layer4_frameworks/odata_services/user_executor.py

Preserves the wire contract (C6) after the requests -> httpx migration:
- OData param encoding ($ -> %24, space -> %20, quote_via=quote);
- 422 -> ValueError("Invalid JSON payload sent to the service");
- 5xx -> RuntimeError("Service internal error");
- network error -> RuntimeError("Failed to call service: ...");
- Authorization header set only when a token is provided.
"""
import httpx
import pytest
from unittest.mock import MagicMock, patch

from app.layer1_domain.entities.odata_query import ODataQuery
from app.layer1_domain.entities.query_result import QueryResult

MODULE = "app.layer4_frameworks.odata_services.user_executor"


@pytest.fixture
def repo():
    with patch(f"{MODULE}.AppSettingsAdapter") as mock_settings:
        mock_settings.return_value.PROFILE_MANAGEMENT_BASE_URL = "https://zz.com"
        from app.layer4_frameworks.odata_services.user_executor import (
            UserQueryRepository,
        )
        yield UserQueryRepository()


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient as an async context manager."""

    response = None
    get_error = None
    last_url = None
    last_headers = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        _FakeAsyncClient.last_url = url
        _FakeAsyncClient.last_headers = headers
        if _FakeAsyncClient.get_error is not None:
            raise _FakeAsyncClient.get_error
        return _FakeAsyncClient.response


def _mock_response(status_code=200, json_value=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_value if json_value is not None else {}
    resp.raise_for_status.return_value = None
    return resp


def _patch_client():
    _FakeAsyncClient.get_error = None
    return patch(f"{MODULE}.httpx.AsyncClient", _FakeAsyncClient)


@pytest.mark.asyncio
async def test_execute_single_success_and_encoding(repo):
    _FakeAsyncClient.response = _mock_response(
        200, {"users": [{"id": 1, "name": "John"}]}
    )
    with _patch_client():
        query = ODataQuery(
            filter="name eq 'John'", top=10, skip=0, count=True
        )
        result = await repo._execute_single(query)

    assert isinstance(result, QueryResult)
    assert result.users == [{"id": 1, "name": "John"}]
    # urlencode() encodes '$' as '%24' and space as '%20'
    assert "%24filter=name%20eq%20%27John%27" in result.odata_query
    assert _FakeAsyncClient.last_url.startswith("https://zz.com/api/v1/users?")


@pytest.mark.asyncio
async def test_execute_single_422_error(repo):
    _FakeAsyncClient.response = _mock_response(422, {"detail": "Invalid filter"})
    with _patch_client():
        with pytest.raises(ValueError) as exc:
            await repo._execute_single(ODataQuery(filter="bad filter"))
    assert "Invalid JSON payload sent to the service" in str(exc.value)


@pytest.mark.asyncio
async def test_execute_single_500_error(repo):
    _FakeAsyncClient.response = _mock_response(500, {"detail": "Database unavailable"})
    with _patch_client():
        with pytest.raises(RuntimeError) as exc:
            await repo._execute_single(ODataQuery(filter="name eq 'John'"))
    assert "Service internal error" in str(exc.value)


@pytest.mark.asyncio
async def test_execute_single_network_error(repo):
    with _patch_client():
        _FakeAsyncClient.get_error = httpx.RequestError("Connection failed")
        with pytest.raises(RuntimeError) as exc:
            await repo._execute_single(ODataQuery(filter="name eq 'John'"))
    assert "Failed to call service" in str(exc.value)


@pytest.mark.asyncio
async def test_execute_single_sets_authorization_header_when_token(repo):
    _FakeAsyncClient.response = _mock_response(200, {"users": []})
    with _patch_client():
        await repo._execute_single(ODataQuery(filter="name eq 'John'"), token="abc123")
    assert _FakeAsyncClient.last_headers["Authorization"] == "Bearer abc123"


@pytest.mark.asyncio
async def test_execute_single_no_authorization_header_when_token_none(repo):
    _FakeAsyncClient.response = _mock_response(200, {"users": []})
    with _patch_client():
        await repo._execute_single(ODataQuery(filter="name eq 'John'"), token=None)
    assert "Authorization" not in _FakeAsyncClient.last_headers


# regression: a body-less GET must NOT send Content-Type, or the profile-management
# backend tries to parse a JSON body and returns 500 "Failed to retrieve users".
@pytest.mark.asyncio
async def test_execute_single_get_sends_no_content_type(repo):
    _FakeAsyncClient.response = _mock_response(200, {"users": []})
    with _patch_client():
        await repo._execute_single(ODataQuery(filter="name eq 'John'"), token="abc123")
    headers = _FakeAsyncClient.last_headers
    assert "Content-Type" not in headers and "content-type" not in headers
    assert headers["accept"] == "application/json"
