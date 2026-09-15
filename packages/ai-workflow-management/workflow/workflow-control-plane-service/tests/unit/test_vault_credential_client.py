"""T2 — VaultCredentialClient resolves a credential id to a __credentials entry (F16, Q3).

REST (Q3): GET /api/v1/internal/credentials/{id}/resolve. The vault returns a
self-describing ResolvedCredential; the client folds the secret into the
type-specific config slot the worker coalesce expects (bearer->token,
api_key->value, basic->password, webhook_url->url). Fail-closed: 404/403 raise.
"""

import base64
import json
import httpx
import pytest

from app.layer4_frameworks.clients.http_vault_credential_client import (
    VaultCredentialClient,
    CredentialNotFound,
    CredentialResolveAuthError,
)


def _client(handler) -> VaultCredentialClient:
    return VaultCredentialClient(
        base_url="http://vault.test",
        service_token="svc-token",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_api_key_folds_secret_into_value_slot():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer svc-token"
        assert request.url.path == "/api/v1/internal/credentials/cred_123/resolve"
        assert request.url.params["tenant_id"] == "t1"
        return httpx.Response(200, json={
            "auth_type": "api_key",
            "config": {"header": "X-API-Key"},
            "secret": base64.b64encode(b"k").decode(),
            "expires_at": None,
        })

    out = await _client(handler).resolve_by_id("t1", "cred_123")
    assert out["type"] == "api_key"
    assert out["config"] == {"header": "X-API-Key", "value": "k"}


@pytest.mark.asyncio
async def test_basic_folds_secret_into_password_slot():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "auth_type": "basic",
            "config": {"username": "u"},
            "secret": base64.b64encode(b"p").decode(),
            "expires_at": None,
        })

    out = await _client(handler).resolve_by_id("t1", "c")
    assert out["config"] == {"username": "u", "password": "p"}


@pytest.mark.asyncio
async def test_webhook_url_folds_secret_into_url_slot():
    # ms-teams-webhook: the URL IS the credential, so the vault sends it in
    # `secret` under auth_type="webhook_url" and the client folds it into
    # config["url"] — the exact shape ms-teams-worker's coalesce reads
    # (execute_task_usecase: type == "webhook_url", config["url"]).
    # The docstring above named this fold from the start but nothing exercised
    # it, which is how the vault/worker shape mismatch stayed invisible.
    url = "https://acme.webhook.office.com/webhookb2/abc/IncomingWebhook/def"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "auth_type": "webhook_url",
            "config": {},
            "secret": base64.b64encode(url.encode()).decode(),
            "expires_at": None,
        })

    out = await _client(handler).resolve_by_id("t1", "c")
    assert out["type"] == "webhook_url"
    assert out["config"] == {"url": url}


@pytest.mark.asyncio
async def test_404_raises_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "missing"})

    with pytest.raises(CredentialNotFound):
        await _client(handler).resolve_by_id("t1", "nope")


@pytest.mark.asyncio
async def test_403_raises_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "forbidden"})

    with pytest.raises(CredentialResolveAuthError):
        await _client(handler).resolve_by_id("t1", "c")


@pytest.mark.asyncio
async def test_issue_credential_posts_and_returns_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/internal/credentials"
        body = json.loads(request.content)
        assert body == {"tenant_id": "t1", "auth_type": "bearer",
                        "config": {"token": "jwt-123"}}
        return httpx.Response(201, json={"id": "cred_new"})

    cred_id = await _client(handler).issue_credential(
        "t1", "bearer", {"token": "jwt-123"}
    )
    assert cred_id == "cred_new"


@pytest.mark.asyncio
async def test_issue_credential_403_raises_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "forbidden"})

    with pytest.raises(CredentialResolveAuthError):
        await _client(handler).issue_credential("t1", "api_key", {"value": "k"})
