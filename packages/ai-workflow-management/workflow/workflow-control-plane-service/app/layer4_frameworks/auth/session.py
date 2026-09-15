"""Session + cookie primitives for the Keycloak BFF (SA-2530).

The browser only ever holds an opaque, Fernet-encrypted cookie value; the real
OIDC tokens live server-side in the ``SessionStore``. That is the whole point
of the BFF shape — an XSS on the SPA cannot read an access token, because the
token is never in the browser.
"""
import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken


def _derive_key(secret: str) -> bytes:
    """Derive a stable 32-byte urlsafe Fernet key from an arbitrary secret."""
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


class CookieCodec:
    """Encrypt/authenticate small JSON payloads for cookies (Fernet = AES-CBC + HMAC)."""

    def __init__(self, secret: str) -> None:
        self._f = Fernet(_derive_key(secret))

    def encode(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return self._f.encrypt(raw).decode()

    def decode(self, token: str, ttl: Optional[int] = None) -> Optional[dict[str, Any]]:
        try:
            raw = self._f.decrypt(token.encode(), ttl=ttl)
            return json.loads(raw)
        except (InvalidToken, ValueError, json.JSONDecodeError):
            return None


@dataclass
class Session:
    user: dict[str, Any]
    access_token: str
    refresh_token: Optional[str]
    id_token: Optional[str]
    expires_at: float
    # SA-2531 — when the *access token* expires (≤ expires_at). Drives the
    # silent refresh: near this instant we swap the token via the refresh grant.
    access_expires_at: Optional[float] = None


class SessionStore:
    """In-memory session store with TTL eviction.

    Single-instance only. For multi-pod Cloud Foundry deployments back this
    with Redis (``settings.REDIS_URL`` is already available) — the interface is
    deliberately tiny so swapping the backend is a drop-in follow-up. Kept
    in-memory here to keep SA-2530 focused and reviewable.
    """

    def __init__(self) -> None:
        self._data: dict[str, Session] = {}

    def create(self, session: Session) -> str:
        sid = secrets.token_urlsafe(32)
        self._data[sid] = session
        return sid

    def get(self, sid: str) -> Optional[Session]:
        session = self._data.get(sid)
        if session is None:
            return None
        if session.expires_at < time.time():
            self._data.pop(sid, None)
            return None
        return session

    def delete(self, sid: str) -> None:
        self._data.pop(sid, None)


_store: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    """Process-wide singleton session store."""
    global _store
    if _store is None:
        _store = SessionStore()
    return _store
