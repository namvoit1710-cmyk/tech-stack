"""Observability endpoints: /status, /metrics, /traces."""

from __future__ import annotations

import platform
import time
from typing import Any

from fastapi import APIRouter, Query, Request, Response

router = APIRouter(tags=["observability"])

_BOOT_TIME = time.time()


@router.get("/status")
def instance_status(request: Request) -> dict[str, Any]:
    """Runtime status of this executor instance."""
    container = request.app.state.container

    monitor = container.get("_monitor")
    trace_store = container.get("_trace_store")
    concurrency = container.get("_concurrency_control")
    pending_store = container.get("_pending_task_store")
    registry_store = container.get("_registry_store")

    from app.layer4_frameworks.config.app_config import settings

    status: dict[str, Any] = {
        "service": settings.APP_NAME,
        "service_name": settings.SERVICE_NAME,
        "infra_mode": settings.INFRA_MODE,
        "uptime_seconds": round(time.time() - _BOOT_TIME, 1),
        "python_version": platform.python_version(),
        "hostname": platform.node(),
    }

    # Concurrency
    if concurrency:
        status["concurrency"] = {
            "active_tasks": concurrency.active_count,
            "max_concurrent": concurrency.max_concurrent,
            "available_slots": concurrency.max_concurrent - concurrency.active_count,
        }

    # Pending async tasks
    if pending_store and hasattr(pending_store, "count"):
        status["pending_async_tasks"] = pending_store.count()

    # Registered workers
    if registry_store and hasattr(registry_store, "list_all"):
        workers = registry_store.list_all()
        status["registered_workers"] = len(workers)
        status["worker_types"] = list({w.worker_type for w in workers if w.worker_type})

    # Trace stats
    if trace_store:
        status["trace_stats"] = trace_store.stats

    # Config
    status["config"] = {
        "task_timeout_seconds": settings.TASK_TIMEOUT_SECONDS,
        "sweep_interval_seconds": settings.SWEEP_INTERVAL_SECONDS,
        "workflow_control_plane_url": settings.WORKFLOW_CONTROL_PLANE_URL,
        "callback_base_url": settings.CALLBACK_BASE_URL or f"http://localhost:{settings.SERVICE_PORT}",
    }

    return status


@router.get("/metrics")
def prometheus_metrics(request: Request) -> Response:
    """Prometheus-compatible metrics endpoint."""
    container = request.app.state.container
    monitor = container.get("_monitor")

    # Update gauges with current values before scrape
    concurrency = container.get("_concurrency_control")
    pending_store = container.get("_pending_task_store")
    registry_store = container.get("_registry_store")

    if monitor and hasattr(monitor, "set_active_tasks") and concurrency:
        monitor.set_active_tasks(concurrency.active_count)
    if monitor and hasattr(monitor, "set_pending_tasks") and pending_store and hasattr(pending_store, "count"):
        monitor.set_pending_tasks(pending_store.count())
    if monitor and hasattr(monitor, "set_registered_workers") and registry_store and hasattr(registry_store, "list_all"):
        monitor.set_registered_workers(len(registry_store.list_all()))

    if monitor and hasattr(monitor, "metrics_output"):
        body, content_type = monitor.metrics_output()
        return Response(content=body, media_type=content_type)

    return Response(content=b"# no metrics available\n", media_type="text/plain")


@router.get("/traces")
def list_traces(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """List recent completed task traces."""
    trace_store = request.app.state.container.get("_trace_store")
    if not trace_store:
        return {"traces": [], "message": "Trace store not available"}
    return {"traces": trace_store.get_recent(limit)}


@router.get("/traces/active")
def active_traces(request: Request) -> dict[str, Any]:
    """List currently active (in-flight) task traces."""
    trace_store = request.app.state.container.get("_trace_store")
    if not trace_store:
        return {"traces": [], "message": "Trace store not available"}
    return {"traces": trace_store.get_active()}


@router.get("/traces/{task_id}")
def get_trace(request: Request, task_id: str) -> dict[str, Any]:
    """Get a specific task trace by task_id."""
    trace_store = request.app.state.container.get("_trace_store")
    if not trace_store:
        return {"error": "Trace store not available"}
    trace = trace_store.get_by_task_id(task_id)
    if trace is None:
        return {"error": f"No trace found for task_id={task_id}"}
    return trace
