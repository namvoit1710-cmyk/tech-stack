"""Controller exposing expression syntax reference."""
import dataclasses

from fastapi import APIRouter, Depends, Request

from app.layer2_application.features.get_expression_reference.use_cases.get_expression_reference_use_case import (
    GetExpressionReferenceUseCase,
    GetExpressionReferenceCommand,
)
from typing import Any

router = APIRouter()


def _get_use_case(request: Request) -> GetExpressionReferenceUseCase:
    return request.app.state.container["get_expression_reference"]


@router.get("/reference")
async def get_expression_reference(
    usecase: GetExpressionReferenceUseCase = Depends(_get_use_case),
) -> Any:
    result = await usecase.execute(GetExpressionReferenceCommand())
    return dataclasses.asdict(result)
