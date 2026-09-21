"""Unit specs for OAuthTokenProvider (layer4 auth).

Target: app/layer4_frameworks/auth/oauth_token_provider.py

`get_token` is async. It reads module-level `settings`, POSTs via httpx.AsyncClient,
and maps every non-Unauthorized failure to UnauthorizedException. Grounded branches:
- no TOKEN_URL -> UnauthorizedException("No token source configured.")
- success -> resp.json()["access_token"]
- any httpx/HTTP error -> UnauthorizedException("Failed to obtain token from token_url.")
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.layer4_frameworks.auth.oauth_token_provider import OAuthTokenProvider
from app.layer1_domain.entities.exceptions import UnauthorizedException


MODULE = "app.layer4_frameworks.auth.oauth_token_provider"


def _mock_async_client(post_return=None, post_side_effect=None):
    """Build a context-manager mock mimicking `async with httpx.AsyncClient() as c`."""
    client = AsyncMock()
    if post_side_effect is not None:
        client.post.side_effect = post_side_effect
    else:
        client.post.return_value = post_return
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client


# invalid input / edge: no TOKEN_URL configured -> Unauthorized, no HTTP call
@pytest.mark.asyncio
async def test_get_token_no_token_url_raises_unauthorized():
    with patch(f"{MODULE}.settings") as s:
        s.TOKEN_URL = ""
        provider = OAuthTokenProvider()
        with pytest.raises(UnauthorizedException) as exc:
            await provider.get_token()
    assert exc.value.detail == "No token source configured."


# happy path: token url set, 200 with access_token -> returns the token
@pytest.mark.asyncio
async def test_get_token_success_returns_access_token():
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"access_token": "abc.def.ghi"}
    cm, client = _mock_async_client(post_return=resp)

    with patch(f"{MODULE}.settings") as s, patch(
        f"{MODULE}.httpx.AsyncClient", return_value=cm
    ):
        s.TOKEN_URL = "https://token.example/oauth"
        s.CLIENT_ID = "cid"
        s.CLIENT_SECRET = "csecret"
        provider = OAuthTokenProvider()
        token = await provider.get_token()

    assert token == "abc.def.ghi"
    # client_credentials grant sent
    _, kwargs = client.post.call_args
    assert kwargs["data"]["grant_type"] == "client_credentials"
    assert kwargs["data"]["client_id"] == "cid"


# failure path: raise_for_status raises -> mapped to Unauthorized (generic message)
@pytest.mark.asyncio
async def test_get_token_http_error_maps_to_unauthorized():
    resp = MagicMock()
    resp.raise_for_status.side_effect = Exception("500 server error")
    cm, _ = _mock_async_client(post_return=resp)

    with patch(f"{MODULE}.settings") as s, patch(
        f"{MODULE}.httpx.AsyncClient", return_value=cm
    ):
        s.TOKEN_URL = "https://token.example/oauth"
        s.CLIENT_ID = "cid"
        s.CLIENT_SECRET = "csecret"
        provider = OAuthTokenProvider()
        with pytest.raises(UnauthorizedException) as exc:
            await provider.get_token()

    assert exc.value.detail == "Failed to obtain token from token_url."


# failure path: transport error on POST -> also mapped to Unauthorized
@pytest.mark.asyncio
async def test_get_token_post_transport_error_maps_to_unauthorized():
    cm, _ = _mock_async_client(post_side_effect=RuntimeError("conn refused"))

    with patch(f"{MODULE}.settings") as s, patch(
        f"{MODULE}.httpx.AsyncClient", return_value=cm
    ):
        s.TOKEN_URL = "https://token.example/oauth"
        s.CLIENT_ID = "cid"
        s.CLIENT_SECRET = "csecret"
        provider = OAuthTokenProvider()
        with pytest.raises(UnauthorizedException) as exc:
            await provider.get_token()

    assert exc.value.detail == "Failed to obtain token from token_url."


# boundary: success response missing access_token key -> KeyError becomes Unauthorized
# (source catches broad Exception after raise_for_status passes)
@pytest.mark.asyncio
async def test_get_token_missing_access_token_key_maps_to_unauthorized():
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"something_else": "x"}
    cm, _ = _mock_async_client(post_return=resp)

    with patch(f"{MODULE}.settings") as s, patch(
        f"{MODULE}.httpx.AsyncClient", return_value=cm
    ):
        s.TOKEN_URL = "https://token.example/oauth"
        s.CLIENT_ID = "cid"
        s.CLIENT_SECRET = "csecret"
        provider = OAuthTokenProvider()
        with pytest.raises(UnauthorizedException) as exc:
            await provider.get_token()

    assert exc.value.detail == "Failed to obtain token from token_url."
