import dataclasses

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from typing import Any

from app.layer2_application.features.store_output.use_cases.store_output_use_case import StoreOutputUseCase, StoreOutputCommand
from app.layer2_application.features.get_output.use_cases.get_output_use_case import GetOutputUseCase, GetOutputCommand


# ── Request DTOs ──────────────────────────────────────────────────


class StoreOutputRequest(BaseModel):
    run_id: str
    data: dict[str, Any] = {}
    reference: str = ""


# ── Dependency helpers ────────────────────────────────────────────


def _get_store_output(request: Request) -> StoreOutputUseCase:
    return request.app.state.container["store_output"]


def _get_get_output(request: Request) -> GetOutputUseCase:
    return request.app.state.container["get_output"]


# ── Router ────────────────────────────────────────────────────────

router = APIRouter()


@router.post("")
async def store_output(
    payload: StoreOutputRequest,
    usecase: StoreOutputUseCase = Depends(_get_store_output),
) -> Any:
    command = StoreOutputCommand(**payload.model_dump())
    result = await usecase.execute(command)
    return dataclasses.asdict(result)


@router.get("/{run_id}")
async def get_output(
    run_id: str,
    usecase: GetOutputUseCase = Depends(_get_get_output),
) -> Any:
    command = GetOutputCommand(run_id=run_id)
    result = await usecase.execute(command)
    return dataclasses.asdict(result)
