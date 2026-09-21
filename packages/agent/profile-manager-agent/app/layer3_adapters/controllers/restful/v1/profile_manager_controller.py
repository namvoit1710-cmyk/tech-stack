from fastapi import APIRouter, Request

from app.layer2_application.features.execute_agent.use_cases.execute_graph_use_case import (
    ExecuteGraphCommand,
    ExecuteGraphUseCase,
)
from .dtos.execute_dto import ExecuteInputDto


def create_router(use_case: ExecuteGraphUseCase) -> APIRouter:
    router = APIRouter()

    @router.post("/execute")
    async def execute(payload: ExecuteInputDto, request: Request):
        token: str | None = getattr(request.state, "jwt_token", None)
        command = ExecuteGraphCommand(
            message=payload.message,
            conv_id=payload.conv_id or None,
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
            source=payload.source,
            parameters=payload.parameters,
            token=token,
        )
        # Return the formatted_response dict verbatim to keep the wire shape
        # byte-identical (C5); ExecuteOutputDto documents/validates that shape.
        return await use_case.execute(command)

    return router
