"""Principal + permission-guard FastAPI dependencies (SA RBAC v1, Task 3).

The shared authorization seam for every guarded router: `get_current_principal`
resolves the caller into a `Principal`; `require_permission(*codes)` gates an endpoint
on holding ALL of the given permission codes (`system`/`is_super` principals bypass).

Built here (T3 is the first guarded router); reused by T5 on agent endpoints and
consolidated by T6.
"""

from typing import Awaitable, Callable

from dependency_injector.wiring import Provide, inject
from fastapi import Depends, Request

from app.layer1_domain.entities.principal import Principal
from app.layer1_domain.exceptions import ForbiddenException
from app.layer2_application.use_cases.resolve_principal import ResolvePrincipalUseCase
from container import Container


def get_bearer_token(request: Request) -> str | None:
    """The raw bearer token the `AuthMiddleware` parsed onto `request.state` (or None).

    Single source for reading the token so principal resolution and the (few) endpoints
    that must forward the caller's token to PM don't each reach into `request.state`.
    """
    return getattr(request.state, "jwt_token", None)


@inject
async def get_current_principal(
    token: str | None = Depends(get_bearer_token),
    use_case: ResolvePrincipalUseCase = Depends(Provide[Container.resolve_principal_use_case]),
) -> Principal:
    """Resolve the caller's `Principal` from the request bearer token.

    Raises `UnauthenticatedException` (401) for a token-less call when the Phase-1
    fail-open flag is off, or a token with no id.
    """
    return await use_case.execute(token)


def require_permission(*codes: str) -> Callable[..., Awaitable[Principal]]:
    """FastAPI dependency factory: require the caller to hold ALL of `codes`.

    `system` and `is_super` principals bypass (handled by `Principal.has`). A missing
    permission raises `ForbiddenException` (403). Returns the `Principal` so the
    endpoint can reuse it.
    """

    async def _dependency(
        principal: Principal = Depends(get_current_principal),
    ) -> Principal:
        missing = [code for code in codes if not principal.has(code)]
        if missing:
            raise ForbiddenException(
                f"Missing required permission(s): {', '.join(missing)}"
            )
        return principal

    # Expose the required codes so the guarded routes can be introspected (e.g. the
    # capability-matrix test that pins every endpoint to its permission).
    _dependency.required_permissions = frozenset(codes)
    return _dependency
