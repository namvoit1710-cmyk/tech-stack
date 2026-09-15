import json
from collections.abc import Sequence

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from typing import Any

# Endpoints that should never be wrapped (e.g. Swagger UI, OpenAPI spec)
DEFAULT_IGNORE_PATHS: list[str] = [
    "/docs",
    "/redoc",
    "/openapi.json",
]


class ResponseWrapperMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: Any,
        wrap_prefixes: Sequence[str] = (),
        ignore_paths: Sequence[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.wrap_prefixes = tuple(wrap_prefixes)
        self.ignore_paths = list(ignore_paths if ignore_paths is not None else DEFAULT_IGNORE_PATHS)

    def _should_wrap(self, path: str) -> bool:
        # Always skip ignored paths
        for ignored in self.ignore_paths:
            if path == ignored or path.startswith(ignored + "/"):
                return False
        # If wrap_prefixes is set, only wrap matching paths
        if self.wrap_prefixes:
            return any(path.startswith(p) for p in self.wrap_prefixes)
        return True

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        response = await call_next(request)

        if not self._should_wrap(request.url.path):
            return response

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        # Read the response body
        body_bytes = b""
        async for chunk in response.body_iterator:
            if isinstance(chunk, str):
                body_bytes += chunk.encode("utf-8")
            else:
                body_bytes += chunk

        # Strip content-length so Response recalculates it for the new body
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}

        try:
            body = json.loads(body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=headers,
                media_type=response.media_type,
            )

        # Already wrapped (error handler output or health endpoint)
        if isinstance(body, dict) and body.get("status") in ("ok", "error") and ("data" in body or "error" in body):
            return Response(
                content=json.dumps(body),
                status_code=response.status_code,
                headers=headers,
                media_type="application/json",
            )

        # Wrap in success envelope
        wrapped = {"status": "ok", "data": body}
        return Response(
            content=json.dumps(wrapped),
            status_code=response.status_code,
            headers=headers,
            media_type="application/json",
        )
