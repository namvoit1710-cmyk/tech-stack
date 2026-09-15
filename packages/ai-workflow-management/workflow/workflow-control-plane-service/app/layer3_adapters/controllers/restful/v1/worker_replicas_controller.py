"""REST endpoints for /api/v2/worker-replicas (SA-2047 R10).

Two endpoints, both proxying the Worker Executor's R6 replica-health
endpoints via ``IWorkerRegistryClient``:

- ``GET /worker-replicas``               — per-type aggregate summary
- ``GET /worker-replicas/{worker_type}`` — per-instance detail rows

Both return a BARE JSON array — NOT the ``{status, data}`` envelope that
``ResponseWrapperMiddleware`` applies to the rest of ``/api/v1/*`` and
``/api/v2/*``. The R10 frontend (`workflow-management`'s `worker-replicas.ts`)
types these responses directly as ``T[]``:
``apiV2.get<IWorkerReplicaAggregate[]>("/worker-replicas")``. To honor that,
``/api/v2/worker-replicas`` is carved out of the wrapper's ``ignore_paths``
in ``create_fastapi_app()`` (see the v1 package ``__init__.py``).

Neither route is gated behind ``ENABLE_NODE_CATALOG_V2`` — that flag is
scoped to the node-catalog migration, not this feature.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request

from app.layer2_application.interfaces.worker_registry_client_interface import (
    IWorkerRegistryClient,
)

router = APIRouter(tags=["worker-replicas"])


def _get_registry_client(request: Request) -> Optional[IWorkerRegistryClient]:
    """Same container key the v1 worker-function proxy uses
    (``worker_function_controller.py``'s ``_get_client``) — the CP wires
    exactly one ``HttpWorkerRegistryClient``/``GrpcWorkerRegistryClient``
    instance in ``bootstrap.py`` and exposes it under this key."""
    return request.app.state.container.get("_worker_registry_client")


@router.get("/worker-replicas")
def list_worker_replicas(
    client: Optional[IWorkerRegistryClient] = Depends(_get_registry_client),
) -> Any:
    if client is None:
        return []
    rows = client.get_replica_summary()
    return rows if rows is not None else []


@router.get("/worker-replicas/{worker_type}")
def get_worker_type_replicas(
    worker_type: str,
    client: Optional[IWorkerRegistryClient] = Depends(_get_registry_client),
) -> Any:
    if client is None:
        return []
    rows = client.get_type_replicas(worker_type)
    if not rows:
        return []
    return [_to_worker_instance(row) for row in rows]


def _to_worker_instance(item: dict[str, Any]) -> dict[str, Any]:
    """Map the executor's per-instance shape to the frontend ``IWorkerInstance``:

    executor: {instance_id, status, last_heartbeat, age_seconds, host,
               version, sdk_version, started_at, delivery_mode}
    frontend: {id, instance_id, host, health_status, last_heartbeat,
               age_seconds, version}

    ``id`` mirrors ``instance_id`` (the FE uses ``id`` as a React key, so it
    must be present + unique per instance). ``status`` is renamed to
    ``health_status`` — ``status`` alone would collide with a differently
    shaped field elsewhere in the FE's API surface.
    """
    instance_id = item.get("instance_id")
    return {
        "id": instance_id,
        "instance_id": instance_id,
        "name": item.get("name") or instance_id,
        "host": item.get("host"),
        "health_status": item.get("status"),
        "last_heartbeat": item.get("last_heartbeat"),
        "age_seconds": item.get("age_seconds"),
        "version": item.get("version"),
    }
