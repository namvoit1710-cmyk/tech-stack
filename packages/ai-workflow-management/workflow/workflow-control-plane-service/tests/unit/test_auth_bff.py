"""Unit tests for the Keycloak BFF endpoints (SA-2530).

Mounts only the auth router (no DB / container) and mocks the OIDC network
back-channel, so the full login round-trip is exercised in-process.
"""
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1.auth_controller import router as auth_router
from app.layer4_frameworks.auth import oidc_client, session as session_mod
from app.layer4_frameworks.config.app_config import settings


@pytest.fixture
def client(monkeypatch):
    # Configure the BFF and use non-Secure cookies so the test's HTTP cookie
    # jar keeps them (Secure cookies are dropped over http://testserver).
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "KEYCLOAK_ISSUER", "https://kc.example/realms/tenant-1")
    monkeypatch.setattr(settings, "KEYCLOAK_CLIENT_ID", "gateway-bff-fm")
    monkeypatch.setattr(settings, "KEYCLOAK_CLIENT_SECRET", "shh")
    monkeypatch.setattr(settings, "KEYCLOAK_REDIRECT_URI", "https://bff.example/api/v1/auth/callback")
    monkeypatch.setattr(settings, "AUTH_SESSION_SECRET", "unit-test-secret")
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", False)
    monkeypatch.setattr(settings, "AUTH_COOKIE_SAMESITE", "lax")
    monkeypatch.setattr(settings, "AUTH_POST_LOGIN_REDIRECT", "https://spa.example/app")

    # Fresh session store per test.
    monkeypatch.setattr(session_mod, "_store", None)

    async def fake_exchange(self, code, verifier):
        assert code == "the-code" and verifier
        return {
            "access_token": "AT",
            "refresh_token": "RT",
            "id_token": "IDT",
            "expires_in": 300,
        }

    async def fake_userinfo(self, access_token):
        assert access_token == "AT"
        return {"sub": "u-1", "email": "felix@laidon.com", "name": "Felix Do", "realm_access": {"roles": ["user"]}}

    monkeypatch.setattr(oidc_client.OIDCClient, "exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_client.OIDCClient, "fetch_userinfo", fake_userinfo)

    app = FastAPI()
    api = APIRouter()
    api.include_router(auth_router, prefix="/auth")
    app.include_router(api, prefix="/api/v1")
    return TestClient(app)


def test_login_redirects_to_keycloak_with_pkce(client):
    r = client.get("/api/v1/auth/login", follow_redirects=False)
    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("https://kc.example/realms/tenant-1/protocol/openid-connect/auth")
    q = parse_qs(urlparse(loc).query)
    assert q["client_id"] == ["gateway-bff-fm"]
    assert q["response_type"] == ["code"]
    assert q["code_challenge_method"] == ["S256"]
    assert "wf_oauth_state" in r.cookies


def test_full_login_roundtrip_then_me_then_logout(client):
    # /login → capture the state the BFF put in the authorize URL
    login = client.get("/api/v1/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

    # /callback with the matching state → sets wf_session, redirects to the SPA
    cb = client.get(
        f"/api/v1/auth/callback?code=the-code&state={state}",
        follow_redirects=False,
    )
    assert cb.status_code == 307
    assert cb.headers["location"] == "https://spa.example/app"
    assert "wf_session" in cb.cookies

    # /me → the authenticated user (raw object, not wrapped)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["id"] == "u-1"
    assert body["email"] == "felix@laidon.com"
    assert body["roles"] == ["user"]

    # /logout → clears the session; /me is then 401
    out = client.post("/api/v1/auth/logout")
    assert out.status_code == 200
    assert "kc.example/realms/tenant-1/protocol/openid-connect/logout" in out.json()["logout_url"]
    client.cookies.clear()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_callback_rejects_mismatched_state(client):
    client.get("/api/v1/auth/login", follow_redirects=False)
    r = client.get("/api/v1/auth/callback?code=the-code&state=not-the-real-state", follow_redirects=False)
    assert r.status_code == 400


def test_me_without_session_is_401(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_endpoints_are_404_when_auth_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    assert client.get("/api/v1/auth/login", follow_redirects=False).status_code == 404
    assert client.get("/api/v1/auth/me").status_code == 404
