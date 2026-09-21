"""Request ID middleware for tracking requests through the system."""

import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.layer4_infrastructure.logger.app_logger import get_logger

logger = get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add unique request ID to each request for tracing.
    
    Adds X-Request-ID header to requests and responses for distributed tracing.
    Logs request ID in structured logs for correlation.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process request and add request ID to context.
        
        Args:
            request: Incoming HTTP request
            call_next: Next middleware/endpoint in chain
            
        Returns:
            Response with X-Request-ID header
        """
        # Get or generate request ID
        request_id = request.headers.get("X-Request-ID")
        if not request_id:
            request_id = str(uuid.uuid4())
        
        # Add request ID to request state for use in handlers
        request.state.request_id = request_id
        
        logger.info(
            "request_started",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            query_params=str(request.query_params),
        )
        
        # Process request
        try:
            response = await call_next(request)
            
            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id
            
            logger.info(
                "request_completed",
                request_id=request_id,
                status_code=response.status_code,
            )
            
            return response
        except Exception as e:
            logger.error(
                "request_failed",
                request_id=request_id,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )
            raise
