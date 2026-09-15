"""Request DTOs for the /api/v2/node-catalog endpoints."""

from __future__ import annotations

from pydantic import Field

from .base_dto import BaseInputDto

MAX_REFS_PER_BATCH = 50


class BatchRefsDto(BaseInputDto):
    """Payload for POST /api/v2/node-catalog/details.

    Each ref is a string of the form ``<id>`` or ``<id>@<version>``.
    Parsed server-side via :func:`app.layer1_domain.value_objects.node_ref.parse_node_ref`.
    """

    refs: list[str] = Field(..., min_length=1, max_length=MAX_REFS_PER_BATCH)
    tenant_id: str = "system"
