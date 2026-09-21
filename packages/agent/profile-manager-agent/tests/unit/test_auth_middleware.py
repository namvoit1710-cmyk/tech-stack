"""Unit specs for AuthMiddleware.dispatch (layer4 middleware).

Target: app/layer4_frameworks/middleware/auth.py

`dispatch` is async. Collaborators: `token_provider` (its `get_token` is async ->
AsyncMock) and `call_next` (async). Grounded branches:
- Authorization header present -> strip "Bearer " -> request.state.jwt_token
- header absent -> request.state.jwt_token = await token_provider.get_token()
- always returns await call_next(request)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.layer4_frameworks.middleware.auth import AuthMiddleware


def _request(auth_header=None):
    req = MagicMock()
    req.headers.get.return_value = auth_header
    # request.state is a plain attribute bag
    req.state = MagicMock()
    return req


@pytest.fixture
def token_provider():
    tp = AsyncMock()
    tp.get_token.return_value = "provider-token"
    return tp


@pytest.fixture
def middleware(token_provider):
    # BaseHTTPMiddleware wants an ASGI app; a MagicMock suffices for unit scope.
    return AuthMiddleware(app=MagicMock(), token_provider=token_provider)


# happy path: header present -> Bearer stripped, provider NOT called
@pytest.mark.asyncio
async def test_dispatch_uses_bearer_header_token(middleware, token_provider):
    req = _request("Bearer header-token")
    call_next = AsyncMock(return_value="response")

    result = await middleware.dispatch(req, call_next)

    assert req.state.jwt_token == "header-token"
    token_provider.get_token.assert_not_awaited()
    call_next.assert_awaited_once_with(req)
    assert result == "response"


# edge: header without the "Bearer " prefix -> replace() is a no-op, kept as-is
@pytest.mark.asyncio
async def test_dispatch_header_without_bearer_prefix_kept_verbatim(middleware):
    req = _request("raw-token-value")
    call_next = AsyncMock(return_value="ok")

    await middleware.dispatch(req, call_next)

    assert req.state.jwt_token == "raw-token-value"


# failure/edge path: no header -> falls back to token provider
@pytest.mark.asyncio
async def test_dispatch_no_header_falls_back_to_provider(middleware, token_provider):
    req = _request(None)
    call_next = AsyncMock(return_value="ok")

    await middleware.dispatch(req, call_next)

    token_provider.get_token.assert_awaited_once()
    assert req.state.jwt_token == "provider-token"


# invalid input: empty-string header is falsy -> provider fallback (grounded in `if auth_header`)
@pytest.mark.asyncio
async def test_dispatch_empty_header_falls_back_to_provider(middleware, token_provider):
    req = _request("")
    call_next = AsyncMock(return_value="ok")

    await middleware.dispatch(req, call_next)

    token_provider.get_token.assert_awaited_once()
    assert req.state.jwt_token == "provider-token"


# failure path: provider raises -> propagates (dispatch does not catch)
@pytest.mark.asyncio
async def test_dispatch_provider_error_propagates(middleware, token_provider):
    token_provider.get_token.side_effect = RuntimeError("no token")
    req = _request(None)
    call_next = AsyncMock()

    with pytest.raises(RuntimeError, match="no token"):
        await middleware.dispatch(req, call_next)
    call_next.assert_not_awaited()
