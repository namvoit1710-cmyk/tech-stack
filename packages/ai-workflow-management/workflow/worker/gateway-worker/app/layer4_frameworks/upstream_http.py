"""httpx-based upstream caller used by both call_internal and call_external.

Parses JSON response when content-type is JSON, otherwise returns raw text.
Caps response body size (truncates and surfaces a RESPONSE_TOO_LARGE-style result).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class UpstreamResponse:
    status_code: int
    headers: dict[str, str]
    body: Any
    body_truncated: bool = False


class UpstreamTimeout(Exception):
    pass


class UpstreamError(Exception):
    pass


class ResponseTooLarge(Exception):
    pass


class UpstreamHttpClient:
    def __init__(
        self,
        *,
        default_timeout_s: float,
        max_response_bytes: int,
        http_client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self._default_timeout_s = default_timeout_s
        self._max_response_bytes = max_response_bytes
        self._http_client_factory = http_client_factory or _default_factory

    async def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: Any,
        timeout_ms: int | None,
    ) -> UpstreamResponse:
        timeout_s = self._resolve_timeout(timeout_ms)
        kwargs: dict[str, Any] = {"headers": headers}
        if body is not None and method != "GET":
            kwargs["json"] = body
        try:
            async with self._http_client_factory(timeout_s) as client:
                response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeout(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(str(exc)) from exc

        raw = response.content or b""
        truncated = False
        if len(raw) > self._max_response_bytes:
            raise ResponseTooLarge(
                f"response body {len(raw)} bytes exceeds cap {self._max_response_bytes}"
            )

        return UpstreamResponse(
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items()},
            body=_decode_body(response, raw),
            body_truncated=truncated,
        )

    def _resolve_timeout(self, timeout_ms: int | None) -> float:
        if timeout_ms is None:
            return self._default_timeout_s
        return min(self._default_timeout_s, max(0.1, timeout_ms / 1000.0))


def _decode_body(response: httpx.Response, raw: bytes) -> Any:
    content_type = (response.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            return response.json()
        except Exception:
            pass
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _default_factory(timeout_s: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout_s)
