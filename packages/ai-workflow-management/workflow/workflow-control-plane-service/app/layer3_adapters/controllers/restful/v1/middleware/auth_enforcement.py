"""Global API auth enforcement (SA-2533).

When ``AUTH_ENABLED`` is on, every ``/api/v1/*`` request must carry a valid BFF
session cookie OR a valid ``Authorization: Bearer`` JWT (verified against the
realm JWKS). Public routes — the auth endpoints themselves and health — are
exempt. This is what makes an API call / a workflow trigger require auth
(UC-3, UC-9, UC-10, and the UI Execute action which calls the run API).

Off by default (AUTH_ENABLED unset) → the service behaves exactly as before.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.layer3_adapters.controllers.restful.v1.auth_dependencies import resolve_session
from app.layer4_frameworks.auth.jwt_verify import JWTError, verify_access_token
from app.layer4_frameworks.config.app_config import settings

# /api/v1 prefixes that must stay reachable without a session (login flow + probes).
#
# ``/api/v1/webhooks`` is the convergent event-trigger front door: external
# callers (Jira & other systems) POST here to fire event-mode workflows. That
# ingress does its OWN per-registration verification (shared secret / HMAC / IP
# allowlist / tenant binding), so external callers can never hold a Keycloak
# session — layering the BFF session check on top just 401s every event trigger.
# Exempt it so event-mode flows trigger normally; every other /api/v1/* route
# stays Keycloak-protected. NOTE: this only matches the ingress router mounted at
# /webhooks — "/api/v1/webhook-management" does not start with "/api/v1/webhooks".
_PUBLIC_PREFIXES = ("/api/v1/auth", "/api/v1/health", "/api/v1/webhooks")


class AuthEnforcementMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not settings.AUTH_ENABLED:
            return await call_next(request)

        path = request.url.path
        if (
            request.method == "OPTIONS"
            or not path.startswith("/api/v1/")
            or any(path.startswith(p) for p in _PUBLIC_PREFIXES)
        ):
            return await call_next(request)

        if self._authenticated(request):
            return await call_next(request)

        # Echo CORS so the browser can read the 401 (the SPA is a different origin).
        origin = request.headers.get("origin")
        headers = (
            {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true"}
            if origin
            else {}
        )
        return JSONResponse({"detail": "not authenticated"}, status_code=401, headers=headers)

    @staticmethod
    def _authenticated(request) -> bool:
        if resolve_session(request) is not None:
            return True
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                verify_access_token(auth.split(" ", 1)[1].strip())
                return True
            except JWTError:
                return False
        return False
