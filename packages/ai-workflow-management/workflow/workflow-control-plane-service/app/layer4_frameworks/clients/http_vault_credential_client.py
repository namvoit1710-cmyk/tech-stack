"""HTTP adapter for the credentials-management vault — run-time resolve-by-id (Q3 = REST).

Calls the vault's authenticated server-to-server endpoint
``GET /api/v1/internal/credentials/{id}/resolve`` (Part A, S1/S2), which returns a
self-describing, decrypted ``ResolvedCredential``. This client folds the secret into
the type-specific config slot the worker coalesce expects, producing the exact
``{type, config}`` shape that lands in the run's ``__credentials`` map.

Fail-closed: a 404 (missing/revoked) or 403 (wrong caller) raises rather than
returning an empty credential — never silently drop auth.
"""
import base64
import logging
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0

# worker {type} -> the config key the secret folds into. Matches http-request-worker's
# _apply_effective_auth (bearer->token, api_key->value, basic->password) plus the
# webhook_url->url kind added for the Teams worker (Q4).
_SECRET_SLOT = {"bearer": "token", "api_key": "value", "basic": "password", "webhook_url": "url"}


# Re-exported from the domain layer so application-layer code can catch these
# without importing a layer4 HTTP client. Still raised from here; every
# existing ``from ...http_vault_credential_client import CredentialNotFound``
# keeps resolving.
from app.layer1_domain.exceptions import (  # noqa: E402  (kept beside its users)
    CredentialNotFound,
    CredentialResolveAuthError,
)

__all__ = [
    "CredentialNotFound",
    "CredentialResolveAuthError",
    "VaultCredentialClient",
]


class VaultCredentialClient:
    """Resolves a vault credential id into a ``{type, config}`` __credentials entry."""

    def __init__(
        self,
        base_url: str,
        service_token: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        headers = {"Authorization": f"Bearer {service_token}"} if service_token else {}
        self._client = httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT, headers=headers, transport=transport
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Call on application shutdown."""
        await self._client.aclose()

    async def resolve_by_id(
        self, tenant_id: str, credential_id: str, user_id: str | None = None
    ) -> dict[str, Any]:
        """Resolve a credential id to a ``{type, config, expires_at}`` entry.

        ``config`` already has the secret folded into its type-specific slot, so the
        result is ready to drop into ``__credentials[credential_ref]``. Tenant is the
        run's tenant, always sent (TENANT_BLEED) — the vault enforces it server-side.
        """
        url = f"{self._base_url}/api/v1/internal/credentials/{credential_id}/resolve"
        params = {"tenant_id": tenant_id}
        if user_id:
            params["user_id"] = user_id

        response = await self._client.get(url, params=params)

        if response.status_code == 404:
            raise CredentialNotFound(credential_id)
        if response.status_code in (401, 403):
            raise CredentialResolveAuthError(
                f"vault rejected resolve of {credential_id!r}: {response.status_code}"
            )
        response.raise_for_status()

        body = response.json()
        auth_type = body.get("auth_type", "none")
        config = dict(body.get("config") or {})
        secret_b64 = body.get("secret")
        slot = _SECRET_SLOT.get(auth_type)
        if slot and secret_b64:
            config[slot] = base64.b64decode(secret_b64).decode()

        expires_at = body.get("expires_at")
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))

        return {"type": auth_type, "config": config, "expires_at": expires_at}

    async def issue_credential(
        self, tenant_id: str, kind: str, config: dict[str, Any]
    ) -> str:
        """Issue a new vault credential and return its id (T5 migration counterpart).

        The inverse of :meth:`resolve_by_id`: the T5 migration calls this to lift an
        existing inline node secret into the vault, then stores the returned id as the
        node's ``credential_ref``. ``config`` carries the secret in its type-specific
        slot (e.g. ``{"token": ...}``). Server-to-server; the vault enforces the tenant.
        """
        url = f"{self._base_url}/api/v1/internal/credentials"
        response = await self._client.post(
            url, json={"tenant_id": tenant_id, "auth_type": kind, "config": config}
        )
        if response.status_code in (401, 403):
            raise CredentialResolveAuthError(
                f"vault rejected issue for tenant {tenant_id!r}: {response.status_code}"
            )
        response.raise_for_status()
        body = response.json()
        return body.get("id") or body.get("credential_id")
