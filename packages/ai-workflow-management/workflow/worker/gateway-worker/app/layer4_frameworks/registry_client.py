"""Calls the control-plane `_resolve` endpoint and caches the response.

The cache is per-(service-name) with a configurable TTL. An asyncio lock per
key dedupes concurrent misses so the worker doesn't fan out N requests to
the control-plane on cold start.

A "not found" / "soft-deleted" response is also cached (briefly) so a misspelled
service name doesn't storm the CP if a node retries quickly.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx


_NEGATIVE_CACHE_TTL_S = 10.0


@dataclass(frozen=True)
class ResolvedService:
    base_url: str
    auth_header_name: str
    auth_header_value: str
    auth_type: str


class ServiceNotFound(Exception):
    pass


class RegistryUnavailable(Exception):
    pass


@dataclass
class _CacheEntry:
    expires_at: float
    resolved: ResolvedService | None  # None = negative cache (not found)


class InternalServiceRegistryClient:
    def __init__(
        self,
        *,
        control_plane_url: str,
        ttl_s: float,
        http_client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self._base_url = control_plane_url.rstrip("/")
        self._ttl_s = ttl_s
        self._cache: dict[str, _CacheEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._http_client_factory = http_client_factory or _default_http_client_factory

    async def resolve(self, service_name: str) -> ResolvedService:
        now = time.monotonic()
        cached = self._cache.get(service_name)
        if cached and cached.expires_at > now:
            if cached.resolved is None:
                raise ServiceNotFound(f"service {service_name!r} not found (cached)")
            return cached.resolved

        lock = self._locks.setdefault(service_name, asyncio.Lock())
        async with lock:
            # Re-check after acquiring the lock — another coroutine may have populated.
            cached = self._cache.get(service_name)
            now = time.monotonic()
            if cached and cached.expires_at > now:
                if cached.resolved is None:
                    raise ServiceNotFound(f"service {service_name!r} not found (cached)")
                return cached.resolved
            resolved = await self._fetch(service_name)
        return resolved

    async def _fetch(self, service_name: str) -> ResolvedService:
        url = f"{self._base_url}/api/v1/gateway/services/{service_name}/_resolve"
        try:
            async with self._http_client_factory() as client:
                response = await client.post(url)
        except httpx.HTTPError as exc:
            raise RegistryUnavailable(f"registry call failed: {exc}") from exc

        if response.status_code == 404:
            self._cache[service_name] = _CacheEntry(
                expires_at=time.monotonic() + _NEGATIVE_CACHE_TTL_S,
                resolved=None,
            )
            raise ServiceNotFound(f"service {service_name!r} not found")
        if response.status_code != 200:
            raise RegistryUnavailable(
                f"registry returned {response.status_code} for {service_name}"
            )

        try:
            envelope = response.json()
        except Exception as exc:
            raise RegistryUnavailable(f"registry returned invalid JSON: {exc}") from exc
        if envelope.get("status") != "ok":
            raise RegistryUnavailable(
                f"registry envelope was not ok: {envelope.get('error')}"
            )
        data = envelope.get("data") or {}
        resolved = ResolvedService(
            base_url=data.get("base_url", ""),
            auth_header_name=data.get("auth_header_name", ""),
            auth_header_value=data.get("auth_header_value", ""),
            auth_type=data.get("auth_type", ""),
        )
        if not resolved.base_url:
            raise RegistryUnavailable(
                f"registry response missing base_url for {service_name}"
            )
        self._cache[service_name] = _CacheEntry(
            expires_at=time.monotonic() + self._ttl_s,
            resolved=resolved,
        )
        return resolved

    def invalidate(self, service_name: str) -> None:
        """Drop a cached entry. Public for tests / future event-driven invalidation."""
        self._cache.pop(service_name, None)


def _default_http_client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=5.0)


# ── Gateway App-Function resolve (P2) ─────────────────────────────────


@dataclass(frozen=True)
class ResolvedAppFunction:
    """The CP's call-spec for a registered gateway function."""
    kind: str                       # "internal" | "external"
    base_url: str                   # internal target
    allowed_hosts: tuple[str, ...]  # external allowlist
    method: str
    path_template: str
    params: tuple[dict, ...]        # [{key, location, required, type}, ...]
    timeout_ms: int
    auth_header_name: str
    auth_header_value: str


@dataclass
class _FnCacheEntry:
    expires_at: float
    resolved: ResolvedAppFunction | None  # None = negative cache


class AppFunctionResolveClient:
    """Resolves a gateway function into a call-spec via the control plane.

    Addressable two ways — by id, or by (app, function) name. Ids are minted
    per-environment, so a node that pins one cannot be promoted dev → qa → prod
    unchanged; the name pair is stable. Both share one TTL-cache + per-key-lock
    (the same pattern as InternalServiceRegistryClient), so a name and an id that
    denote the same function simply occupy two cache slots — harmless, and it
    keeps the negative cache and the concurrent-miss dedupe working for both.
    Reuses ServiceNotFound / RegistryUnavailable.
    """

    def __init__(
        self,
        *,
        control_plane_url: str,
        ttl_s: float,
        http_client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self._base_url = control_plane_url.rstrip("/")
        self._ttl_s = ttl_s
        self._cache: dict[str, _FnCacheEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._http_client_factory = http_client_factory or _default_http_client_factory

    async def resolve(self, function_id: str) -> ResolvedAppFunction:
        """Resolve by function id."""
        return await self._resolve_cached(
            cache_key=f"id:{function_id}",
            label=f"function {function_id!r}",
            request=lambda client: client.post(
                f"{self._base_url}/api/v1/gateway/functions/{function_id}/_resolve"
            ),
        )

    async def resolve_by_name(self, app: str, function: str) -> ResolvedAppFunction:
        """Resolve by (app name, function name) — the environment-portable form."""
        return await self._resolve_cached(
            cache_key=f"name:{app}/{function}",
            label=f"function {function!r} of app {app!r}",
            request=lambda client: client.post(
                f"{self._base_url}/api/v1/gateway/functions/_resolve",
                json={"app": app, "function": function},
            ),
        )

    async def _resolve_cached(
        self,
        *,
        cache_key: str,
        label: str,
        request: Callable[[httpx.AsyncClient], Any],
    ) -> ResolvedAppFunction:
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and cached.expires_at > now:
            if cached.resolved is None:
                raise ServiceNotFound(f"{label} not found (cached)")
            return cached.resolved

        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            # Re-check: another coroutine may have populated while we waited.
            cached = self._cache.get(cache_key)
            now = time.monotonic()
            if cached and cached.expires_at > now:
                if cached.resolved is None:
                    raise ServiceNotFound(f"{label} not found (cached)")
                return cached.resolved
            return await self._fetch(cache_key, label, request)

    async def _fetch(
        self,
        cache_key: str,
        label: str,
        request: Callable[[httpx.AsyncClient], Any],
    ) -> ResolvedAppFunction:
        try:
            async with self._http_client_factory() as client:
                response = await request(client)
        except httpx.HTTPError as exc:
            raise RegistryUnavailable(f"resolve call failed: {exc}") from exc

        if response.status_code == 404:
            self._cache[cache_key] = _FnCacheEntry(
                expires_at=time.monotonic() + _NEGATIVE_CACHE_TTL_S, resolved=None,
            )
            raise ServiceNotFound(f"{label} not found")
        if response.status_code != 200:
            raise RegistryUnavailable(
                f"resolve returned {response.status_code} for {label}"
            )
        try:
            envelope = response.json()
        except Exception as exc:
            raise RegistryUnavailable(f"resolve returned invalid JSON: {exc}") from exc
        if envelope.get("status") != "ok":
            raise RegistryUnavailable(
                f"resolve envelope was not ok: {envelope.get('error')}"
            )
        data = envelope.get("data") or {}
        resolved = ResolvedAppFunction(
            kind=data.get("kind", ""),
            base_url=data.get("base_url", ""),
            allowed_hosts=tuple(data.get("allowed_hosts") or ()),
            method=data.get("method", "GET"),
            path_template=data.get("path_template", ""),
            params=tuple(data.get("params") or ()),
            timeout_ms=int(data.get("timeout_ms") or 0),
            auth_header_name=data.get("auth_header_name", ""),
            auth_header_value=data.get("auth_header_value", ""),
        )
        if not resolved.path_template:
            raise RegistryUnavailable(
                f"resolve response missing path_template for {label}"
            )
        self._cache[cache_key] = _FnCacheEntry(
            expires_at=time.monotonic() + self._ttl_s, resolved=resolved,
        )
        return resolved

    def invalidate(self, function_id: str) -> None:
        self._cache.pop(f"id:{function_id}", None)

    def invalidate_by_name(self, app: str, function: str) -> None:
        self._cache.pop(f"name:{app}/{function}", None)
