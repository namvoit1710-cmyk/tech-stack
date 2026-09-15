"""Unit tests for the global API auth enforcement middleware (SA-2533)."""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1.middleware.auth_enforcement import (
    AuthEnforcementMiddleware,
)
from app.layer4_frameworks.auth import session as session_mod
from app.layer4_frameworks.auth.session import CookieCodec, Session, get_session_store
from app.layer4_frameworks.config.app_config import settings


def _app():
    app = FastAPI()
    app.add_middleware(AuthEnforcementMiddleware)

    @app.get("/api/v1/health")
    def health():
        return {"ok": True}

    @app.get("/api/v1/auth/me")
    def me():
        return {"exempt": True}

    @app.get("/api/v1/workflows")
    def workflows():
        return {"data": []}

    @app.post("/api/v1/webhooks/{path:path}")
    def webhook_ingress(path: str):
        return {"accepted": path}

    @app.get("/api/v1/webhook-management")
    def webhook_management():
        return {"data": []}

    return TestClient(app)


def test_noop_when_auth_disabled(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    assert _app().get("/api/v1/workflows").status_code == 200


def test_protected_route_401_without_auth(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_SESSION_SECRET", "s")
    assert _app().get("/api/v1/workflows").status_code == 401


def test_health_and_auth_paths_exempt(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_SESSION_SECRET", "s")
    c = _app()
    assert c.get("/api/v1/health").status_code == 200
    assert c.get("/api/v1/auth/me").status_code == 200


def test_webhook_ingress_exempt_but_management_protected(monkeypatch):
    # The event-trigger front door is public (it self-verifies per registration);
    # the webhook-management CRUD API stays behind Keycloak.
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_SESSION_SECRET", "s")
    c = _app()
    assert c.post("/api/v1/webhooks/jira/incoming").status_code == 200
    assert c.get("/api/v1/webhook-management").status_code == 401


def test_valid_session_cookie_passes(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_SESSION_SECRET", "s")
    monkeypatch.setattr(settings, "AUTH_SESSION_COOKIE", "wf_session")
    monkeypatch.setattr(session_mod, "_store", None)
    sid = get_session_store().create(
        Session(user={"id": "u"}, access_token="AT", refresh_token=None, id_token=None,
                expires_at=time.time() + 300)
    )
    c = _app()
    c.cookies.set("wf_session", CookieCodec("s").encode({"sid": sid}))
    assert c.get("/api/v1/workflows").status_code == 200
