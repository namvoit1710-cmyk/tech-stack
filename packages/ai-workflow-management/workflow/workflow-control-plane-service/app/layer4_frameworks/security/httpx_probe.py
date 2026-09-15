"""httpx-based implementation of IHttpProbe."""
from __future__ import annotations

import time

import httpx

from app.layer2_application.interfaces.http_probe_interface import HttpProbeResult


async def httpx_probe(
    url: str,
    headers: dict[str, str],
    timeout_s: float,
) -> HttpProbeResult:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(url, headers=headers)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return HttpProbeResult(
            reachable=True,
            status_code=response.status_code,
            latency_ms=elapsed_ms,
        )
    except httpx.HTTPError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return HttpProbeResult(
            reachable=False,
            latency_ms=elapsed_ms,
            error=str(exc),
        )
