"""Parsed request shapes for call_internal / call_external.

`from_dict` raises `InvalidParams` for any caller mistake. Functions catch
this and return the structured `{error: {code, message}}` shape required by
the invoke_function envelope (see workflow-gateway-design.md §2.4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


class InvalidParams(ValueError):
    pass


@dataclass(frozen=True)
class CallInternalParams:
    service: str
    endpoint: str
    method: str
    headers: dict[str, str]
    body: Any
    timeout_ms: int | None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CallInternalParams":
        service = raw.get("service")
        if not isinstance(service, str) or not service.strip():
            raise InvalidParams("`service` is required and must be a non-empty string")
        endpoint = raw.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise InvalidParams("`endpoint` is required and must be a non-empty string")
        method = (raw.get("method") or "GET").upper()
        if method not in _ALLOWED_METHODS:
            raise InvalidParams(f"`method` must be one of {sorted(_ALLOWED_METHODS)}")
        headers = raw.get("headers") or {}
        if not isinstance(headers, dict):
            raise InvalidParams("`headers` must be an object")
        timeout_ms = raw.get("timeout_ms")
        if timeout_ms is not None and (not isinstance(timeout_ms, (int, float)) or timeout_ms <= 0):
            raise InvalidParams("`timeout_ms` must be a positive number when provided")
        return cls(
            service=service.strip(),
            endpoint=endpoint.strip(),
            method=method,
            headers={str(k): str(v) for k, v in headers.items()},
            body=raw.get("body"),
            timeout_ms=int(timeout_ms) if timeout_ms is not None else None,
        )


@dataclass(frozen=True)
class CallExternalParams:
    url: str
    method: str
    headers: dict[str, str]
    body: Any
    timeout_ms: int | None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CallExternalParams":
        url = raw.get("url")
        if not isinstance(url, str) or not url.strip():
            raise InvalidParams("`url` is required and must be a non-empty string")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise InvalidParams("`url` must be absolute (http:// or https://)")
        method = (raw.get("method") or "GET").upper()
        if method not in _ALLOWED_METHODS:
            raise InvalidParams(f"`method` must be one of {sorted(_ALLOWED_METHODS)}")
        headers = raw.get("headers") or {}
        if not isinstance(headers, dict):
            raise InvalidParams("`headers` must be an object")
        timeout_ms = raw.get("timeout_ms")
        if timeout_ms is not None and (not isinstance(timeout_ms, (int, float)) or timeout_ms <= 0):
            raise InvalidParams("`timeout_ms` must be a positive number when provided")
        return cls(
            url=url.strip(),
            method=method,
            headers={str(k): str(v) for k, v in headers.items()},
            body=raw.get("body"),
            timeout_ms=int(timeout_ms) if timeout_ms is not None else None,
        )


@dataclass
class GatewayResult:
    """Function return shape. Either body fields populated, OR `error` populated."""
    status_code: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None
    error: dict[str, Any] | None = None

    @classmethod
    def gateway_error(cls, code: str, message: str) -> "GatewayResult":
        return cls(error={"code": code, "message": message})

    def to_dict(self) -> dict[str, Any]:
        if self.error is not None:
            return {"error": self.error}
        return {
            "status_code": self.status_code,
            "headers": self.headers,
            "body": self.body,
        }
