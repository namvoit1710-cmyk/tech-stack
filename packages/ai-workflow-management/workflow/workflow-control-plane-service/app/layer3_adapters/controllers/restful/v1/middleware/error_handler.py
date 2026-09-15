from fastapi import Request
from fastapi.responses import JSONResponse
from app.layer1_domain.exceptions import (
    AppError,
    EntityNotFound,
    ValidationError,
    WorkflowExecutionError,
    PermissionDeniedError,
    InfrastructureError,
)
from app.layer2_application.errors import ErrorCode
from app.layer2_application.repositories.run_repository_interface import (
    OptimisticConcurrencyError,
)
from typing import Any


async def app_error_handler(request: Request, exc: AppError) -> Any:
    if isinstance(exc, EntityNotFound):
        return JSONResponse(status_code=404, content={
            "status": "error",
            "error": {"code": "NOT_FOUND", "message": str(exc)},
        })
    if isinstance(exc, ValidationError):
        return JSONResponse(status_code=400, content={
            "status": "error",
            "error": {"code": "VALIDATION_ERROR", "message": str(exc)},
        })
    if isinstance(exc, WorkflowExecutionError):
        return JSONResponse(status_code=422, content={
            "status": "error",
            "error": {"code": "EXECUTION_ERROR", "message": str(exc)},
        })
    if isinstance(exc, PermissionDeniedError):
        return JSONResponse(status_code=403, content={
            "status": "error",
            "error": {"code": "PERMISSION_DENIED", "message": str(exc)},
        })
    if isinstance(exc, InfrastructureError):
        return JSONResponse(status_code=500, content={
            "status": "error",
            "error": {"code": "INFRASTRUCTURE_ERROR", "message": str(exc)},
        })
    return JSONResponse(status_code=500, content={
        "status": "error",
        "error": {"code": "INTERNAL_ERROR", "message": str(exc)},
    })


async def optimistic_concurrency_handler(
    request: Request, exc: OptimisticConcurrencyError,
) -> Any:
    """Phase M.2.1: surface CAS conflicts as HTTP 409. Use cases that
    raise this exception (propagated from ``IRunRepository.save``) get
    a uniform response without each needing its own try/except."""
    code = ErrorCode.OPTIMISTIC_CONCURRENCY_CONFLICT
    return JSONResponse(status_code=code.http_status, content={
        "status": "error",
        "error": {
            "code": code.code,
            "message": code.default_message,
            "details": {
                "run_id": exc.run_id,
                "expected_version": exc.expected,
                "actual_version": exc.actual,
            },
        },
    })


async def generic_error_handler(request: Request, exc: Exception) -> Any:
    return JSONResponse(status_code=500, content={
        "status": "error",
        "error": {"code": "INTERNAL_ERROR", "message": "Internal server error"},
    })
