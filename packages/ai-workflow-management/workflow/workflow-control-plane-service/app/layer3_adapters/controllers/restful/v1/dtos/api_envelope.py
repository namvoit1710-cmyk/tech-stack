"""Generic response envelope for OpenAPI typing.

The existing ``ResponseWrapperMiddleware`` already wraps every response body
in ``{status, data | error}`` at runtime. This module declares the matching
Pydantic *types* so that FastAPI's OpenAPI generator can advertise the full
response shape (both success and error) on every route.

Usage in a controller::

    from app.layer3_adapters.controllers.restful.v1.dtos.api_envelope import (
        ApiEnvelope, ApiErrorPayload,
    )

    @router.get(
        "/runs/{run_id}/nodes/{node_id}/schema",
        response_model=ApiEnvelope[GetNodeSchemaResponse],
        responses={
            404: {"model": ApiEnvelope[ApiErrorPayload]},
            409: {"model": ApiEnvelope[ApiErrorPayload]},
        },
    )
    async def get_node_schema(...): ...
"""

from __future__ import annotations

from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiErrorPayload(BaseModel):
    """Shape of the ``error`` field when ``status == "error"``.

    Matches the existing ``ErrorEnvelope.error`` shape produced by
    ``response_envelope.error_response()`` and by ``app_error_handler``.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code": "PRECONDITION_FAILED",
                    "message": "Node input was modified since last read.",
                    "details": {"current_etag": 'W/"sha256:abcd1234"'},
                },
                {
                    "code": "VALIDATION_FAILED",
                    "message": "validation failed",
                    "details": {
                        "ops": [
                            {"op_index": 0, "field": "qty", "rule": "gte",
                             "message": "must be >= 0", "value": -5},
                        ],
                    },
                },
            ],
        },
    )

    code: str = Field(
        ...,
        examples=["PRECONDITION_FAILED", "VALIDATION_FAILED", "NODE_NOT_FOUND"],
        description=(
            "Machine-readable error code. See `layer2_application/errors.py` "
            "`ErrorCode` for the full catalogue."
        ),
    )
    message: str = Field(
        ...,
        description="Human-readable summary. Safe to surface in a toast.",
    )
    details: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Free-form per-error context (e.g., `{current_etag}` for 412, "
            "`{ops: [...]}` for 422 batch validation)."
        ),
    )


class ApiEnvelope(BaseModel, Generic[T]):
    """Response envelope: ``{status: 'ok', data: T}`` or
    ``{status: 'error', error: ApiErrorPayload}``. FastAPI renders the two
    arms as a discriminated union in OpenAPI."""

    status: Literal["ok", "error"] = Field(
        ...,
        description="`ok` for success responses, `error` for any non-2xx.",
    )
    data: Optional[T] = Field(
        default=None,
        description="Success payload. Present on `ok`; absent on `error`.",
    )
    error: Optional[ApiErrorPayload] = Field(
        default=None,
        description="Error payload. Present on `error`; absent on `ok`.",
    )
