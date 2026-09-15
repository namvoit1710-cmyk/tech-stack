"""Keycloak BFF endpoints (SA-2530): ``/login`` ``/callback`` ``/logout`` ``/me``.

Mounted at ``/api/v1/auth``. The SPA never sees an OIDC token — it holds an
opaque HttpOnly cookie and the tokens live in the server-side session store.

Flow (Authorization Code + PKCE):

    GET /login          → 307 to Keycloak authorize (state+PKCE in a short-lived
                          encrypted cookie); Keycloak brokers to Azure.
    GET /callback       → exchange code for tokens, read /userinfo, create the
                          server-side session, set the wf_session cookie, 307
                          back to the SPA.
    GET /me             → the current user (401 if no session).
    POST /logout        → drop the session + cookie, return the Keycloak
                          end-session URL for the SPA to navigate to.
"""
import logging
import secrets
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.layer3_adapters.controllers.restful.v1.auth_dependencies import resolve_session
from app.layer4_frameworks.auth.oidc_client import OIDCClient, make_pkce
from app.layer4_frameworks.auth.session import CookieCodec, Session, get_session_store
from app.layer4_frameworks.config.app_config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_STATE_COOKIE = "wf_oauth_state"
_STATE_TTL_SECONDS = 600  # 10 minutes to complete the round-trip


def _codec() -> CookieCodec:
    return CookieCodec(settings.AUTH_SESSION_SECRET)


def _require_enabled() -> None:
    if not settings.AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="auth disabled")
    if not settings.AUTH_SESSION_SECRET or not settings.KEYCLOAK_ISSUER:
        raise HTTPException(status_code=500, detail="auth misconfigured")


def _cookie_kwargs(max_age: int | None = None) -> dict:
    kwargs: dict = {
        "httponly": True,
        "secure": settings.AUTH_COOKIE_SECURE,
        "samesite": settings.AUTH_COOKIE_SAMESITE,
        "path": "/",
    }
    if settings.AUTH_COOKIE_DOMAIN:
        kwargs["domain"] = settings.AUTH_COOKIE_DOMAIN
    if max_age is not None:
        kwargs["max_age"] = max_age
    return kwargs


@router.get("/login")
async def login(request: Request, redirect: str | None = None) -> RedirectResponse:
    _require_enabled()
    oidc = OIDCClient()
    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(24)
    post_login = redirect or settings.AUTH_POST_LOGIN_REDIRECT

    response = RedirectResponse(oidc.build_authorize_url(state, challenge), status_code=307)
    response.set_cookie(
        _STATE_COOKIE,
        _codec().encode({"s": state, "v": verifier, "r": post_login}),
        **_cookie_kwargs(max_age=_STATE_TTL_SECONDS),
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    _require_enabled()
    if error:
        raise HTTPException(status_code=400, detail=f"sso error: {error}")

    raw = request.cookies.get(_STATE_COOKIE)
    flow = _codec().decode(raw, ttl=_STATE_TTL_SECONDS) if raw else None
    if not code or not flow or flow.get("s") != state:
        raise HTTPException(status_code=400, detail="invalid or expired auth state")

    oidc = OIDCClient()
    try:
        tokens = await oidc.exchange_code(code, flow["v"])
        userinfo = await oidc.fetch_userinfo(tokens["access_token"])
    except httpx.HTTPError:
        logger.exception("OIDC code exchange failed")
        raise HTTPException(status_code=502, detail="sso exchange failed")

    lifetime = int(tokens.get("expires_in", settings.AUTH_SESSION_TTL_SECONDS))
    realm_roles = (userinfo.get("realm_access") or {}).get("roles") or []
    session = Session(
        user={
            "id": userinfo.get("sub"),
            "email": userinfo.get("email"),
            "name": userinfo.get("name") or userinfo.get("preferred_username"),
            "roles": userinfo.get("roles") or realm_roles,
        },
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token"),
        id_token=tokens.get("id_token"),
        expires_at=time.time() + min(lifetime, settings.AUTH_SESSION_TTL_SECONDS),
        access_expires_at=time.time() + lifetime,
    )
    sid = get_session_store().create(session)

    response = RedirectResponse(flow["r"], status_code=307)
    response.set_cookie(
        settings.AUTH_SESSION_COOKIE,
        _codec().encode({"sid": sid}),
        **_cookie_kwargs(max_age=settings.AUTH_SESSION_TTL_SECONDS),
    )
    response.delete_cookie(_STATE_COOKIE, path="/")
    return response


async def _refresh_if_needed(session, skew: int = 60) -> None:
    """SA-2531 — silently refresh the access token when it is within ``skew``
    seconds of expiry and we still hold a refresh token. Mutates the session in
    place (it lives by reference in the store). Failures are swallowed: the
    caller keeps the current token until it hard-expires and the user re-logs in.
    """
    if not session.refresh_token or session.access_expires_at is None:
        return
    if session.access_expires_at - time.time() > skew:
        return
    try:
        tokens = await OIDCClient().refresh(session.refresh_token)
    except httpx.HTTPError:
        logger.info("token refresh failed; keeping current session until expiry")
        return
    now = time.time()
    lifetime = int(tokens.get("expires_in", settings.AUTH_SESSION_TTL_SECONDS))
    session.access_token = tokens["access_token"]
    session.refresh_token = tokens.get("refresh_token", session.refresh_token)
    session.id_token = tokens.get("id_token", session.id_token)
    session.access_expires_at = now + lifetime
    session.expires_at = now + min(lifetime, settings.AUTH_SESSION_TTL_SECONDS)


@router.get("/me")
async def me(request: Request) -> JSONResponse:
    _require_enabled()
    session = resolve_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    await _refresh_if_needed(session)
    return JSONResponse(session.user)


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    _require_enabled()
    raw = request.cookies.get(settings.AUTH_SESSION_COOKIE)
    data = _codec().decode(raw) if raw else None
    id_token_hint: str | None = None
    if data and data.get("sid"):
        store = get_session_store()
        session = store.get(data["sid"])
        id_token_hint = session.id_token if session else None
        store.delete(data["sid"])

    logout_url = OIDCClient().build_logout_url(settings.AUTH_POST_LOGOUT_REDIRECT, id_token_hint)
    response = JSONResponse({"logout_url": logout_url})
    response.delete_cookie(settings.AUTH_SESSION_COOKIE, path="/")
    return response
