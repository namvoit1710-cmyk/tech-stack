import dataclasses
import logging

from fastapi import APIRouter, Depends, Request, Response

from app.layer2_application.features.list_node_palette.use_cases.list_node_palette_use_case import ListNodePaletteUseCase, ListNodePaletteCommand
from app.layer4_frameworks.config.app_config import settings
from typing import Any


logger = logging.getLogger(__name__)

# Advance-notice window for clients to migrate off /api/v1/node-palette.
# Kept as a constant so operators can grep-and-bump as rollout moves.
_SUNSET_DATE = "Thu, 31 Dec 2026 00:00:00 GMT"


# ── Dependency helpers ────────────────────────────────────────────


def _get_list_node_palette(request: Request) -> ListNodePaletteUseCase:
    return request.app.state.container["list_node_palette"]


# ── Router ────────────────────────────────────────────────────────

router = APIRouter()


@router.get("")
async def list_node_palette(
    request: Request,
    response: Response,
    tenant_id: str = "system",
    usecase: ListNodePaletteUseCase = Depends(_get_list_node_palette),
) -> Any:
    command = ListNodePaletteCommand(tenant_id=tenant_id)
    result = await usecase.execute(command)
    # When v2 is live, mark the legacy endpoint as deprecated so clients
    # surface a migration warning in their devtools. Header-only — body
    # and status unchanged for backward compatibility.
    if getattr(settings, "ENABLE_NODE_CATALOG_V2", False):
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = _SUNSET_DATE
        response.headers["Link"] = '</api/v2/node-catalog>; rel="successor-version"'
        # Log caller identity so operators can chase down lingering legacy
        # clients during rollout. Paired with the
        # node_palette_legacy_requests_total counter in the use case.
        logger.info(
            "legacy /node-palette call",
            extra={
                "tenant_id": tenant_id,
                "user_agent": request.headers.get("user-agent", ""),
                "x_forwarded_for": request.headers.get("x-forwarded-for", ""),
            },
        )
    return dataclasses.asdict(result)

