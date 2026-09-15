"""Keycloak Admin API client (SA-2570).

Central-identity strategy: Keycloak is the single source of truth for *who* the
users are (and, later, their roles + permissions). The workflow owner picker
lists the realm's users straight from Keycloak rather than a local mirror.

Uses the confidential client's own service account (``client_credentials``) to
call ``GET /admin/realms/{realm}/users``. The service account needs the
``realm-management`` role ``view-users`` (or ``query-users``); without it the
call 403s and we degrade to an empty list rather than erroring the endpoint.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class KeycloakAdminClient:
    def __init__(self, issuer: str | None, client_id: str | None, client_secret: str | None) -> None:
        self._issuer = (issuer or "").rstrip("/")  # e.g. https://host/realms/tenant-1
        self._client_id = client_id or ""
        self._client_secret = client_secret or ""
        # Short-lived cache so resolving owner→name on the workflow list doesn't
        # hit Keycloak on every request. (expires_monotonic, users).
        self._cache: tuple[float, list[dict[str, Any]]] | None = None
        self._cache_ttl = 60.0

    @property
    def configured(self) -> bool:
        return bool(self._issuer and self._client_id and self._client_secret)

    @property
    def _token_url(self) -> str:
        return f"{self._issuer}/protocol/openid-connect/token"

    @property
    def _admin_users_url(self) -> str:
        # .../realms/{realm}  ->  .../admin/realms/{realm}/users
        base, _, realm = self._issuer.rpartition("/realms/")
        return f"{base}/admin/realms/{realm}/users"

    async def _token(self, client: httpx.AsyncClient) -> str:
        resp = await client.post(
            self._token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    async def list_users(self, limit: int = 500) -> list[dict[str, Any]]:
        """Return the realm's human users as ``{id, email, name}`` dicts.

        ``id`` is the Keycloak user id (== the token ``sub``), so it matches the
        ``owner`` / ``created_by`` we persist on a workflow. Service-account users
        are excluded. Any failure (unconfigured, network, missing role) yields an
        empty list — the picker simply shows nobody rather than breaking.
        """
        if not self.configured:
            return []
        cached = self._cache
        if cached is not None and cached[0] > time.monotonic():
            return cached[1]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                token = await self._token(client)
                resp = await client.get(
                    self._admin_users_url,
                    params={"max": limit, "enabled": "true"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                users: list[dict[str, Any]] = []
                for u in resp.json():
                    username = u.get("username") or ""
                    if username.startswith("service-account-"):
                        continue  # not a real person
                    name = (
                        " ".join(p for p in [u.get("firstName"), u.get("lastName")] if p)
                        or username
                        or u.get("email")
                        or u.get("id")
                    )
                    users.append({"id": u.get("id"), "email": u.get("email"), "name": name})
                self._cache = (time.monotonic() + self._cache_ttl, users)
                return users
        except Exception as exc:  # noqa: BLE001 — degrade, never break the endpoint
            logger.warning("Keycloak list-users failed: %s", exc)
            return self._cache[1] if self._cache is not None else []

    async def name_map(self) -> dict[str, str]:
        """``{user_id: display_name}`` for resolving owner/created_by ids server-side."""
        return {u["id"]: u["name"] for u in await self.list_users() if u.get("id")}
