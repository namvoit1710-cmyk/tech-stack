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


def error_response(code: str, message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"status": "error", "error": {"code": code, "message": message}},
    )
