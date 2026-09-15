"""Controller exposing Socket.IO event configuration."""
import dataclasses

from fastapi import APIRouter, Depends, Request

from app.layer2_application.features.get_socketio_config.use_cases.get_socketio_config_use_case import (
    GetSocketioConfigUseCase,
    GetSocketioConfigCommand,
)
from typing import Any

router = APIRouter()


def _get_use_case(request: Request) -> GetSocketioConfigUseCase:
    return request.app.state.container["get_socketio_config"]


@router.get("")
async def get_socketio_config(
    usecase: GetSocketioConfigUseCase = Depends(_get_use_case),
) -> Any:
    result = await usecase.execute(GetSocketioConfigCommand())
    return dataclasses.asdict(result)
