"""FastAPI dependencies that resolve the current user from the BFF session (SA-2530).

``get_current_user`` is meant to replace the legacy ``X-User`` header trust on
protected routes. While ``AUTH_ENABLED`` is off it yields the historical
``system`` identity so existing controllers and tests are unaffected; wiring it
into individual controllers is a follow-up (SA-2532/SA-2533).
"""
from typing import Any, Optional

from fastapi import HTTPException, Request

from app.layer4_frameworks.auth.session import CookieCodec, Session, get_session_store
from app.layer4_frameworks.config.app_config import settings


def resolve_session(request: Request) -> Optional[Session]:
    """Return the live server-side session for this request, or None."""
    raw = request.cookies.get(settings.AUTH_SESSION_COOKIE)
    if not raw or not settings.AUTH_SESSION_SECRET:
        return None
    data = CookieCodec(settings.AUTH_SESSION_SECRET).decode(raw)
    if not data or not data.get("sid"):
        return None
    return get_session_store().get(data["sid"])


def get_current_user(request: Request) -> dict[str, Any]:
    """Resolve the caller identity.

    - AUTH off  → the legacy ``X-User`` header (default ``system``).
    - AUTH on   → the BFF session; 401 when there is no valid session.
    """
    if not settings.AUTH_ENABLED:
        legacy = request.headers.get("X-User", "system")
        return {"id": legacy, "email": None, "name": legacy, "roles": []}

    session = resolve_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return session.user


def optional_user(request: Request) -> Optional[dict[str, Any]]:
    """Like ``get_current_user`` but never raises — None when unauthenticated."""
    session = resolve_session(request)
    return session.user if session else None


def require_jwt(request: Request) -> dict[str, Any]:
    """SA-2533 — authenticate a forwarded ``Authorization: Bearer`` JWT.

    Verifies signature (realm JWKS) + issuer / audience / expiry, and returns the
    caller identity. For token-presenting callers (service-to-service, or any
    backend that receives a forwarded Bearer) as opposed to the browser session.
    """
    from app.layer4_frameworks.auth.jwt_verify import JWTError, verify_access_token

    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        claims = verify_access_token(header.split(" ", 1)[1].strip())
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from exc

    return {
        "id": claims.get("sub"),
        "email": claims.get("email"),
        "name": claims.get("name") or claims.get("preferred_username"),
        "roles": (claims.get("realm_access") or {}).get("roles", []),
        "claims": claims,
    }


def resolve_actor(request: Request) -> dict[str, Any]:
    """SA-2570 — the verified caller identity for attribution.

    Unifies both auth paths so run/workflow attribution never trusts the request
    body:

    - AUTH off  → the legacy ``X-User`` header (default ``system``).
    - AUTH on   → the BFF session cookie, else a forwarded ``Authorization:
      Bearer`` JWT (machine-to-machine).

    Never raises — the enforcement middleware already 401s unauthenticated
    callers on protected routes — falling back to ``system`` if nothing resolves.
    """
    if not settings.AUTH_ENABLED:
        legacy = request.headers.get("X-User", "system")
        return {"id": legacy, "email": None, "name": legacy, "roles": []}

    session = resolve_session(request)
    if session is not None:
        return session.user

    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        from app.layer4_frameworks.auth.jwt_verify import JWTError, verify_access_token
        try:
            claims = verify_access_token(header.split(" ", 1)[1].strip())
            return {
                "id": claims.get("sub"),
                "email": claims.get("email"),
                "name": claims.get("name") or claims.get("preferred_username"),
                "roles": (claims.get("realm_access") or {}).get("roles", []),
                "claims": claims,
            }
        except JWTError:
            pass

    return {"id": "system", "email": None, "name": "system", "roles": []}


def actor_label(identity: dict[str, Any] | None) -> str:
    """SA-2558 — the display label to persist as a run's ``triggered_by``.

    Picks the friendliest stable identifier from a resolved actor
    (``name`` -> ``email`` -> ``id``), falling back to the reserved ``system``
    sentinel for automated / M2M / unauthenticated callers. Never returns an
    empty string, so Execution History always has something to render.
    """
    identity = identity or {}
    label = (
        (identity.get("name") or "").strip()
        or (identity.get("email") or "").strip()
        or (identity.get("id") or "").strip()
    )
    return label or "system"


def is_admin(identity: dict[str, Any]) -> bool:
    """True when the caller holds an admin-ish realm role.

    A role whose name contains ``admin`` (case-insensitive) is treated as admin —
    covers ``admin``, ``realm-admin``, ``workflow-admin`` without a config knob.
    """
    return any("admin" in str(r).lower() for r in (identity.get("roles") or []))
