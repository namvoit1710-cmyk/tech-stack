"""SA-2307 — CORS config + middleware ordering (dev/test scope).

Verifies the two changes made to bootstrap.create_app():
  1. CORSMiddleware is the OUTERMOST user middleware (registered LAST) so it handles the
     preflight OPTIONS before Auth/RequestID/RateLimit.
  2. Dev/test CORS posture: allow_credentials=False, origins straight from
     settings.api_cors_origins, and NO br10 allow_origin_regex — which lets a browser dev
     origin (localhost) pass the preflight.

NOTE (verified vs Starlette 0.41.3): making CORS the outermost USER middleware does NOT put
it above ServerErrorMiddleware, so a true 500 still lacks CORS headers — out of scope here.
"""
import os

# Settings requires these 4 external URLs (Field(...) with no default). Set before importing
# bootstrap so create_app() builds without depending on a local .env.
os.environ.setdefault("FETCH_WORKFLOW_URL", "http://localhost/wf")
os.environ.setdefault("FETCH_CURRENT_USER_INFO_URL", "http://localhost/me")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_URL", "http://localhost/groups")
os.environ.setdefault("FETCH_CURRENT_USER_GROUPS_DETAILS_URL", "http://localhost/groups/d")

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from bootstrap import create_app


@pytest.fixture(scope="module")
def app():
    return create_app()


def _cors(app):
    for mw in app.user_middleware:
        if mw.cls is CORSMiddleware:
            return mw
    return None


def test_cors_is_outermost_user_middleware(app):
    """Starlette applies middleware in reverse registration order → the last-registered is
    user_middleware[0] (outermost). CORS must sit there."""
    assert app.user_middleware, "no user middleware registered"
    assert app.user_middleware[0].cls is CORSMiddleware, (
        "CORSMiddleware must be registered LAST so it is the outermost user middleware; "
        f"got {app.user_middleware[0].cls.__name__}"
    )


def test_cors_dev_posture_no_credentials_no_regex(app):
    """Dev/test posture: credentials OFF, origins from settings, br10 regex removed."""
    mw = _cors(app)
    assert mw is not None, "CORSMiddleware not registered"
    settings = app.state.container.settings()
    assert mw.kwargs.get("allow_credentials") is False, "allow_credentials must be False (dev)"
    assert mw.kwargs.get("allow_origins") == settings.api_cors_origins, (
        "allow_origins must be passed straight from settings.api_cors_origins"
    )
    assert not mw.kwargs.get("allow_origin_regex"), "the br10 allow_origin_regex must be removed"


def test_cors_preflight_allows_localhost_dev_origin(app):
    """A localhost dev origin (bearer auth, no credentials) passes the CORS preflight and the
    actual response carries Access-Control-Allow-Origin. Driven through the real CORS config
    pulled from create_app(), on a minimal probe app (no DB / lifespan)."""
    kwargs = _cors(app).kwargs

    probe = FastAPI()
    probe.add_middleware(CORSMiddleware, **kwargs)

    @probe.get("/ping")
    def ping():
        return {"ok": True}

    client = TestClient(probe)

    pre = client.options(
        "/ping",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert pre.status_code == 200
    assert "access-control-allow-origin" in {k.lower() for k in pre.headers}

    res = client.get("/ping", headers={"Origin": "http://localhost:5173"})
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "*"


def test_auth_middleware_sits_inside_cors(app):
    """CORS must WRAP Auth/RequestID/RateLimit (be outermost). In app.user_middleware a lower
    index == more outer, so CORSMiddleware must come before AuthMiddleware."""
    from app.layer4_infrastructure.middleware.auth import AuthMiddleware

    classes = [mw.cls for mw in app.user_middleware]
    assert AuthMiddleware in classes, "AuthMiddleware not registered"
    assert classes.index(CORSMiddleware) < classes.index(AuthMiddleware), (
        "CORSMiddleware must be outside (wrap) AuthMiddleware"
    )
