"""Resolve a target user's identity from PM for mirroring (SA RBAC v1).

Shared by assign-role and pool member-add: given an `external_id` and the caller's own
token, fetch (email, name) from Profile Management, degrading to (None, None) when PM is
unset/unreachable (email is cosmetic; authz keys on external_id + role). Single source
of the PM-mirror protocol so its failure handling has one test seam.
"""

from app.layer1_domain.exceptions import InvalidDataException
from app.layer2_application.interfaces.fetch_current_user_info_api_client import (
    IFetchCurrentUserInfoApiClient,
)
from app.layer2_application.interfaces.logger_interface import ILogger


async def resolve_mirror_target(
    fetch_client: IFetchCurrentUserInfoApiClient,
    external_id: str,
    token: str | None,
    logger: ILogger,
) -> tuple[str | None, str | None]:
    """Return (email, name) for `external_id` via PM using the caller's token.

    No token, or a PM failure, → (None, None) so the caller upserts by external_id only.
    The `logger` (ILogger port) is injected by the calling use case, keeping this service
    free of any layer-4 dependency (strict Clean Architecture).
    """
    if not token:
        return None, None
    try:
        info = await fetch_client.fetch_user_by_external_id(token, external_id)
        return (info.email or None), (info.name or None)
    except InvalidDataException as exc:
        logger.warning(
            "pm_unavailable_on_target_resolve",
            external_id=external_id,
            error=type(exc).__name__,
            detail=str(exc),
        )
        return None, None
