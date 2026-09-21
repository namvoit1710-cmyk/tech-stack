from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from app.layer1_domain.entities.transformation import (
    TransformationAgentData,
    TransformationErrorResult,
    TransformationResponseData,
    TransformationSuccessResult,
)
from app.layer2_application.services.transformation_service import (
    TransformationService,
)


logger = logging.getLogger(__name__)


class ColumnTransformationRule(BaseModel):
    scope: Literal["column"]
    rule_name: str
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None

    model_config = ConfigDict(extra="allow")


class RowTransformationOperation(BaseModel):
    scope: Literal["row"]
    operation: Literal["insert", "delete"]
    row_identifier: dict[str, Any]
    position: Literal["before", "after", "at_index"] | None = None
    data: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


TransformationRule = Annotated[
    ColumnTransformationRule | RowTransformationOperation,
    Field(discriminator="scope"),
]


class TransformationRequest(BaseModel):
    file_id: str
    rules: list[TransformationRule] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


router = APIRouter(tags=["transformations"])


def _get_dependencies(request: Request) -> dict[str, Any]:
    return request.app.state.dependencies


def _get_agent_type(dependencies: dict[str, Any]) -> str:
    settings = dependencies.get("settings")
    return getattr(settings, "AGENT_TYPE", "unknown")


def _build_success_content(result: TransformationSuccessResult) -> str:
    download_url = result.download_url
    if download_url:
        return (
            "Your file transformations finished successfully. "
            f"Download the updated file here: {download_url}"
        )

    if result.file_id:
        return "Your file transformations finished successfully. The updated file is ready to use."

    return "Your file transformations finished successfully."


def _build_error_content(result: TransformationErrorResult) -> str:
    details = result.details or result.error
    if details:
        return f"We couldn't complete your file transformation request. {details}"

    return "We couldn't complete your file transformation request. Please try again."


def _build_success_response(
    result: TransformationSuccessResult,
    agent_type: str,
) -> TransformationResponseData:
    message = _build_success_content(result)

    return TransformationResponseData(
        message=message,
        status="success",
        agent_type=agent_type,
        agent_data=TransformationAgentData(
            agent_type=agent_type,
            valid="correct",
            message=message,
            file_id=result.file_id,
            download_url=result.download_url,
            batch_count=result.batch_count,
        ),
    )


def _build_error_response(
    result: TransformationErrorResult,
    agent_type: str,
) -> TransformationResponseData:
    message = _build_error_content(result)

    return TransformationResponseData(
        message=message,
        status="error",
        agent_type=agent_type,
        agent_data=TransformationAgentData(
            agent_type=agent_type,
            valid="incorrect",
            message=message,
            error=result.error,
            details=result.details,
            failed_batch_index=result.failed_batch_index,
            batch_type=result.batch_type,
            last_successful_file_id=result.last_successful_file_id,
        ),
    )


@router.post("/transformations")
def execute_transformations(
    payload: TransformationRequest,
    request: Request,
):
    dependencies = _get_dependencies(request)
    service: TransformationService = dependencies["data_factory_service"]
    result = service.execute(
        file_id=payload.file_id,
        rules=[rule.model_dump(exclude_none=True) for rule in payload.rules],
    )

    agent_type = _get_agent_type(dependencies)
    if isinstance(result, TransformationSuccessResult) and result.batches:
        logger.info(
            "event=transform_batches batch_count=%d batches=%s",
            len(result.batches),
            [batch.model_dump(exclude_none=True) for batch in result.batches],
        )

    if isinstance(result, TransformationErrorResult):
        logger.error("event=transform_failed result=%s", result.model_dump(exclude_none=True))
        error_response = _build_error_response(result, agent_type)
        return JSONResponse(
            status_code=502,
            content=error_response.model_dump(exclude_none=True),
        )

    return _build_success_response(result, agent_type).model_dump(exclude_none=True)