"""Controller for approval process endpoints.

Provides API to fetch and transform change request approval process data
from the governance service into a graph structure for visualization.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.layer2_application.features.get_approval_process import GetApprovalProcessUseCase
from app.layer2_application.features.get_approval_process.use_cases.get_approval_process_use_case import (
    GetApprovalProcessCommand,
)
from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import error_response


router = APIRouter()


# ── Response DTOs ─────────────────────────────────────────────────


class ApprovalNodeDto(BaseModel):
    """A node in the approval process graph."""

    id: str
    role: str
    name: str | None
    status: str


class ApprovalEdgeDto(BaseModel):
    """An edge connecting two nodes."""

    source: str
    target: str


class ApprovalProcessResponseDto(BaseModel):
    """Response containing the approval process graph."""

    nodes: list[ApprovalNodeDto]
    edges: list[ApprovalEdgeDto]


# ── Dependency helpers ────────────────────────────────────────────


def _get_use_case(request: Request) -> GetApprovalProcessUseCase:
    """Get the use case from the DI container."""
    return request.app.state.container.get("get_approval_process_usecase")


# ── Endpoints ─────────────────────────────────────────────────────


@router.get(
    "/{cr_id}",
    response_model=ApprovalProcessResponseDto,
    summary="Get approval process for a change request",
    description="Fetches approval process data from the governance service and transforms it into a graph structure with nodes and edges.",
)
async def get_approval_process(
    cr_id: str,
    use_case: GetApprovalProcessUseCase = Depends(_get_use_case),
) -> dict[str, Any]:
    """Get the approval process graph for a change request.

    Args:
        cr_id: The change request ID.
        use_case: Injected use case instance.

    Returns:
        Graph structure with nodes and edges for visualization.
    """
    if use_case is None:
        return error_response(
            "APPROVAL_PROCESS_NOT_CONFIGURED",
            "Approval process use case is not configured",
            status_code=500,
        )

    command = GetApprovalProcessCommand(cr_id=cr_id)
    result = await use_case.execute(command)

    if result.error:
        return error_response(
            result.error_code or "APPROVAL_PROCESS_ERROR",
            result.error,
            status_code=400,
        )

    return {
        "nodes": result.nodes,
        "edges": result.edges,
    }
