from fastapi import Request
from fastapi.responses import JSONResponse
from app.layer4_frameworks.config.exceptions import (
    AppError,
    TaskExecutionError,
    WorkerNotFoundError,
    RetryExhaustedError,
)
from typing import Any


async def app_error_handler(request: Request, exc: AppError) -> Any:
    if isinstance(exc, WorkerNotFoundError):
        return JSONResponse(status_code=404, content={
            "status": "error",
            "error": {"code": "NOT_FOUND", "message": str(exc)},
        })
    if isinstance(exc, TaskExecutionError):
        return JSONResponse(status_code=422, content={
            "status": "error",
            "error": {"code": "EXECUTION_ERROR", "message": str(exc)},
        })
    if isinstance(exc, RetryExhaustedError):
        return JSONResponse(status_code=422, content={
            "status": "error",
            "error": {"code": "RETRY_EXHAUSTED", "message": str(exc)},
        })
    return JSONResponse(status_code=500, content={
        "status": "error",
        "error": {"code": "INTERNAL_ERROR", "message": str(exc)},
    })


async def generic_error_handler(request: Request, exc: Exception) -> Any:
    return JSONResponse(status_code=500, content={
        "status": "error",
        "error": {"code": "INTERNAL_ERROR", "message": "Internal server error"},
    })
