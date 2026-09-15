import dataclasses

from pydantic import BaseModel
from typing import Any

from fastapi.responses import JSONResponse


class ErrorDetail(BaseModel):
    code: str
    message: str


class SuccessEnvelope(BaseModel):
    status: str = "ok"
    data: Any


class ErrorEnvelope(BaseModel):
    status: str = "error"
    error: ErrorDetail


def error_response(code: str, message: str, status_code: int = 400, details: dict | None = None) -> JSONResponse:
    error_body: dict[str, Any] = {"code": code, "message": message}
    if details:
        error_body["details"] = details
    return JSONResponse(
        status_code=status_code,
        content={"status": "error", "error": error_body},
    )


def result_or_error(result: Any) -> dict | JSONResponse:
    """Standardized response: check result.error, return dict or error_response.

    Usage::

        result = await usecase.execute(command)
        return result_or_error(result)
    """
    err = getattr(result, "error", None)
    if err:
        error_code_str = getattr(result, "error_code", "")
        details = getattr(result, "error_details", None) or None
        if error_code_str:
            from app.layer2_application.errors import ErrorCode
            ec = ErrorCode.from_code(error_code_str)
            if ec:
                return error_response(ec.code, str(err), ec.http_status, details)
        # Fallback for non-migrated use cases
        code = "NOT_FOUND" if "not found" in str(err).lower() else "BAD_REQUEST"
        status = 404 if code == "NOT_FOUND" else 400
        return error_response(code, str(err), status, details)
    data = dataclasses.asdict(result)
    # Strip empty error fields from success responses
    for key in ("error", "error_code", "error_details"):
        if key in data and not data[key]:
            data.pop(key)
    return data
