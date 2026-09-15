"""ETag middleware — computes a weak ETag over the response body and
honors ``If-None-Match`` with a 304 response.

Scoped to ``path_prefixes`` so it only runs on the v2 catalog endpoints,
not globally. Stays in the middleware stack as OUTER relative to
``ResponseWrapperMiddleware`` — that way the hash is computed over the
already-wrapped ``{status: "ok", data: ...}`` payload, which is what
the client actually sees and echoes back in ``If-None-Match``.

Order of registration (last call = outermost):

    app.add_middleware(ETagMiddleware, path_prefixes=[...])     # inner
    app.add_middleware(ResponseWrapperMiddleware, ...)          # outer
"""

from __future__ import annotations

import hashlib
from typing import Any
from collections.abc import Sequence

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


def _weak_etag(body: bytes) -> str:
    return 'W/"' + hashlib.sha256(body).hexdigest() + '"'


class ETagMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: Any,
        path_prefixes: Sequence[str] = (),
        cache_control: str = "private, max-age=30",
    ) -> None:
        super().__init__(app)
        self._prefixes = tuple(path_prefixes)
        self._cache_control = cache_control

    def _applies(self, path: str) -> bool:
        if not self._prefixes:
            return False
        return any(path.startswith(p) for p in self._prefixes)

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        if not self._applies(request.url.path):
            return await call_next(request)

        response = await call_next(request)
        if response.status_code != 200:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")

        etag = _weak_etag(body)

        if request.headers.get("if-none-match") == etag:
            # 304 must NOT include a body; only the header set.
            return Response(
                status_code=304,
                headers={
                    "ETag": etag,
                    "Cache-Control": self._cache_control,
                },
            )

        # Strip content-length so Response recomputes for the same body.
        out_headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        out_headers["ETag"] = etag
        out_headers["Cache-Control"] = self._cache_control

        return Response(
            content=body,
            status_code=200,
            headers=out_headers,
            media_type=response.media_type,
        )
