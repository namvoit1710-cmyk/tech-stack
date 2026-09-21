"""Error handling middleware for FastAPI application."""
from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.layer1_domain.exceptions import (
    DomainException,
    NotFoundException,
    AlreadyExistsException,
    InvalidDataException,
    InvalidAgentKindException,
    InvalidAgentStatusException,
    InvalidAgentConfigTypeException,
    InvalidOperationException,
    EndpointRequiredException,
    UnauthenticatedException,
    ForbiddenException,
    ConflictException,
)
from app.layer4_infrastructure.logger.app_logger import get_logger

logger = get_logger(__name__)


async def domain_exception_handler(request: Request, exc: DomainException) -> JSONResponse:
    """Handle domain exceptions and convert to appropriate HTTP responses."""
    
    # Log the error
    logger.warning(
        "domain_exception",
        exception_type=type(exc).__name__,
        detail=str(exc),
        path=request.url.path,
    )
    
    # Map domain exceptions to HTTP status codes
    status_map = {
        UnauthenticatedException: status.HTTP_401_UNAUTHORIZED,
        ForbiddenException: status.HTTP_403_FORBIDDEN,
        NotFoundException: status.HTTP_404_NOT_FOUND,
        AlreadyExistsException: status.HTTP_409_CONFLICT,
        ConflictException: status.HTTP_409_CONFLICT,
        InvalidDataException: status.HTTP_400_BAD_REQUEST,
        InvalidAgentKindException: status.HTTP_400_BAD_REQUEST,
        InvalidAgentStatusException: status.HTTP_400_BAD_REQUEST,
        InvalidAgentConfigTypeException: status.HTTP_400_BAD_REQUEST,
        InvalidOperationException: status.HTTP_400_BAD_REQUEST,
        EndpointRequiredException: status.HTTP_400_BAD_REQUEST,
    }

    status_code = status_map.get(type(exc), status.HTTP_400_BAD_REQUEST)

    return JSONResponse(
        status_code=status_code,
        content={
            "error": type(exc).__name__,
            "detail": str(exc),
            "status_code": status_code,
        },
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic validation errors."""
    
    # Convert errors to JSON-serializable format
    errors = []
    for error in exc.errors():
        error_dict = {
            "loc": list(error.get("loc", [])),
            "msg": str(error.get("msg", "")),
            "type": str(error.get("type", "")),
        }
        # Convert ctx values to strings if present
        if "ctx" in error:
            error_dict["ctx"] = {k: str(v) for k, v in error["ctx"].items()}
        errors.append(error_dict)
    
    logger.warning(
        "validation_error",
        errors=errors,
        path=request.url.path,
    )
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "ValidationError",
            "detail": "Request validation failed",
            "validation_errors": errors,
            "status_code": status.HTTP_422_UNPROCESSABLE_ENTITY,
        },
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions."""
    
    logger.error(
        "unexpected_exception",
        exception_type=type(exc).__name__,
        detail=str(exc),
        path=request.url.path,
        exc_info=True,
    )
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "InternalServerError",
            "detail": "An unexpected error occurred",
            "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
        },
    )
