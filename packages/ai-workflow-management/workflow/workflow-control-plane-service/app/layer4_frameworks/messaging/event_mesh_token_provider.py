"""OAuth2 client-credentials token provider for SAP Event Mesh.

Owns the single token cache shared by every component and both refresh paths:
synchronous (token fetch for the management API + consume/ack, on poll/startup
threads) and asynchronous (the publish flusher + a proactive background
refresh). Both paths read/write the same cache, so there is exactly one source
of truth for the current token.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable

from app.layer4_frameworks.messaging.event_mesh_http import EventMeshHttpClients
from app.layer4_frameworks.messaging.event_mesh_metrics import EventMeshMetrics

logger = logging.getLogger(__name__)

_TOKEN_REFRESH_MARGIN_S = 60  # refresh token 60s before expiry


class EventMeshTokenProvider:
    """Cached client-credentials token with sync + async refresh."""

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        clients: EventMeshHttpClients,
        metrics: EventMeshMetrics,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._clients = clients
        self._metrics = metrics

        self._access_token: str = ""
        self._token_expires_at: float = 0.0
        self._lock = threading.Lock()
        self._async_lock: asyncio.Lock | None = None

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def token_expires_at(self) -> float:
        return self._token_expires_at

    # ------------------------------------------------------------------
    # Sync (management API + consume/ack)
    # ------------------------------------------------------------------

    def ensure(self) -> str:
        """Return a valid access token, refreshing if necessary."""
        with self._lock:
            if self._access_token and time.time() < self._token_expires_at:
                return self._access_token
            return self._refresh()

    def _refresh(self) -> str:
        """Fetch a new token via client-credentials grant."""
        try:
            resp = self._clients.sync.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            body = resp.json()
            self._access_token = body["access_token"]
            expires_in = int(body.get("expires_in", 3600))
            self._token_expires_at = time.time() + expires_in - _TOKEN_REFRESH_MARGIN_S
            logger.info("Event Mesh OAuth2 token refreshed (expires_in=%ds)", expires_in)
            self._metrics.record_token_refresh("success")
            return self._access_token
        except Exception:
            self._metrics.record_token_refresh("failure")
            logger.exception("Failed to refresh Event Mesh OAuth2 token")
            raise

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.ensure()}"}

    def amqp_token(self) -> tuple[str, float]:
        """Return ``(access_token, expires_at_epoch)`` for the AMQP SASL auth.

        The proton consumer uses the token as the SASL PLAIN password; the
        expiry is the true one (``token_expires_at`` has the refresh margin
        added back).
        """
        token = self.ensure()
        return token, self._token_expires_at + _TOKEN_REFRESH_MARGIN_S

    # ------------------------------------------------------------------
    # Async (publish flusher + proactive refresh)
    # ------------------------------------------------------------------

    def prepare_async(self) -> None:
        """Create the async lock; call once from the event loop at start_async."""
        self._async_lock = asyncio.Lock()

    async def auth_headers_async(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.ensure_async()}"}

    async def ensure_async(self) -> str:
        """Return a valid access token, refreshing asynchronously if necessary."""
        assert self._async_lock is not None
        async with self._async_lock:
            if self._access_token and time.time() < self._token_expires_at:
                return self._access_token
            return await self._refresh_async()

    async def _refresh_async(self) -> str:
        """Fetch a new token via client-credentials grant over the async client."""
        assert self._clients.async_ is not None
        try:
            resp = await self._clients.async_.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            body = resp.json()
            self._access_token = body["access_token"]
            expires_in = int(body.get("expires_in", 3600))
            self._token_expires_at = time.time() + expires_in - _TOKEN_REFRESH_MARGIN_S
            logger.info(
                "Event Mesh OAuth2 token refreshed async (expires_in=%ds)", expires_in,
            )
            self._metrics.record_token_refresh("success")
            return self._access_token
        except Exception:
            self._metrics.record_token_refresh("failure")
            logger.exception("Failed to refresh Event Mesh OAuth2 token (async)")
            raise

    async def run_refresh_loop(self, should_run: Callable[[], bool]) -> None:
        """Proactively refresh the token at 80% of its remaining TTL.

        Runs until *should_run* returns False (the publisher's running flag).
        """
        try:
            while should_run():
                remaining = max(self._token_expires_at - time.time(), 0.0)
                # If no token yet, refresh after a short warm-up.
                wait_s = 5.0 if not self._access_token else max(60.0, remaining * 0.8)
                try:
                    await asyncio.sleep(wait_s)
                except asyncio.CancelledError:
                    break
                if not should_run():
                    break
                try:
                    await self._refresh_async()
                except Exception:
                    # Retry after 30s on failure; reactive ensure_async is still
                    # available as a safety net.
                    try:
                        await asyncio.sleep(30)
                    except asyncio.CancelledError:
                        break
        except asyncio.CancelledError:
            pass
