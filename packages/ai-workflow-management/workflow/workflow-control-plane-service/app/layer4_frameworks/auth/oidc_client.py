"""Minimal Keycloak OIDC client for the BFF (SA-2530).

Endpoints are derived from the realm issuer using Keycloak's conventional
paths, so only ``KEYCLOAK_ISSUER`` (plus client id/secret/redirect) is needed.

No local JWT signature verification happens here: the tokens arrive over a
direct TLS back-channel from Keycloak (the code exchange), and identity is read
from the ``/userinfo`` endpoint — the transport is the trust boundary.
Downstream services that later receive a forwarded Bearer token still verify
the JWT themselves (SA-2533).
"""
import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx

from app.layer4_frameworks.config.app_config import settings

_HTTP_TIMEOUT = 15.0


def make_pkce() -> tuple[str, str]:
    """Return an (S256) ``(code_verifier, code_challenge)`` pair."""
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


class OIDCClient:
    def __init__(self) -> None:
        self.issuer = settings.KEYCLOAK_ISSUER.rstrip("/")
        self.client_id = settings.KEYCLOAK_CLIENT_ID
        self.client_secret = settings.KEYCLOAK_CLIENT_SECRET
        self.redirect_uri = settings.KEYCLOAK_REDIRECT_URI
        self.scope = settings.KEYCLOAK_SCOPE

    @property
    def authorize_endpoint(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/auth"

    @property
    def token_endpoint(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/token"

    @property
    def userinfo_endpoint(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/userinfo"

    @property
    def end_session_endpoint(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/logout"

    def build_authorize_url(self, state: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "scope": self.scope,
                "redirect_uri": self.redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.authorize_endpoint}?{query}"

    async def exchange_code(self, code: str, code_verifier: str) -> dict:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code_verifier": code_verifier,
        }
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(self.token_endpoint, data=data)
            resp.raise_for_status()
            return resp.json()

    async def refresh(self, refresh_token: str) -> dict:
        """SA-2531 — exchange a refresh token for a fresh token set."""
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(self.token_endpoint, data=data)
            resp.raise_for_status()
            return resp.json()

    async def fetch_userinfo(self, access_token: str) -> dict:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                self.userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            return resp.json()

    def build_logout_url(self, post_logout_redirect: str, id_token_hint: str | None = None) -> str:
        params = {
            "post_logout_redirect_uri": post_logout_redirect,
            "client_id": self.client_id,
        }
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        return f"{self.end_session_endpoint}?{urlencode(params)}"
