import asyncio

import httpx
import pydantic
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.layer1_domain.entities.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
)

from starlette.middleware.base import BaseHTTPMiddleware

class ExceptionHandlerMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
            return response
        except Exception as exc:
            return await self.exception_handler(request, exc)

    async def exception_handler(self, request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, HTTPException):
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "error": exc.__class__.__name__,
                    "detail": exc.detail,
                    "status_code": exc.status_code,
                },
            )

        if isinstance(exc, pydantic.ValidationError):
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content={
                    "error": "ValidationError",
                    "detail": exc.errors(),
                    "status_code": status.HTTP_422_UNPROCESSABLE_CONTENT,
                },
            )

        if isinstance(exc, ValueError):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": "BadRequest",
                    "detail": str(exc),
                    "status_code": status.HTTP_400_BAD_REQUEST,
                },
            )

        if isinstance(exc, UnauthorizedException):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "Unauthorized",
                    "detail": exc.detail,
                    "status_code": status.HTTP_401_UNAUTHORIZED,
                },
            )

        if isinstance(exc, ForbiddenException):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "error": "Forbidden",
                    "detail": exc.detail,
                    "status_code": status.HTTP_403_FORBIDDEN,
                },
            )

        if isinstance(exc, NotFoundException):
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={
                    "error": "NotFound",
                    "detail": exc.detail,
                    "status_code": status.HTTP_404_NOT_FOUND,
                },
            )

        if isinstance(exc, ConflictException):
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "error": "Conflict",
                    "detail": exc.detail,
                    "status_code": status.HTTP_409_CONFLICT,
                },
            )

        if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
            return JSONResponse(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                content={
                    "error": "GatewayTimeout",
                    "detail": "Upstream service timed out",
                    "status_code": status.HTTP_504_GATEWAY_TIMEOUT,
                },
            )

        if isinstance(exc, httpx.ConnectError):
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "error": "ServiceUnavailable",
                    "detail": "Upstream service is unavailable",
                    "status_code": status.HTTP_503_SERVICE_UNAVAILABLE,
                },
            )

        if isinstance(exc, httpx.HTTPStatusError):
            return JSONResponse(
                status_code=status.HTTP_502_BAD_GATEWAY,
                content={
                    "error": "BadGateway",
                    "detail": "Upstream service returned an error",
                    "status_code": status.HTTP_502_BAD_GATEWAY,
                },
            )

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "InternalServerError",
                "detail": "An unexpected error occurred",
                "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
            },
        )
