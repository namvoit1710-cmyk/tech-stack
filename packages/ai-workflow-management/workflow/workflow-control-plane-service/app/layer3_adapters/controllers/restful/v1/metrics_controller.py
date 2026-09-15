"""Prometheus scrape endpoint.

Exposes ``GET /metrics`` returning the Prometheus exposition format
over the monitor's registry. Not wrapped by ResponseWrapperMiddleware
(see explicit ignore in bootstrap) because Prometheus scrapers reject
JSON envelopes.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


router = APIRouter()


@router.get("")
async def get_metrics(request: Request) -> Response:
    monitor = request.app.state.container.get("monitor")
    registry = getattr(monitor, "registry", None)
    if registry is None:
        # No real monitor wired (e.g., stub/dummy in tests that don't use
        # PrometheusMonitor). Return an empty scrape result rather than
        # 500 so operators' Prometheus servers treat this as a working
        # endpoint with zero metrics.
        return Response(content="", media_type=CONTENT_TYPE_LATEST, status_code=200)
    body = generate_latest(registry)
    return Response(content=body, media_type=CONTENT_TYPE_LATEST, status_code=200)
