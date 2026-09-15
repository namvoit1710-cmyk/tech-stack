"""REST endpoints for /api/v2/worker-apps (SA-2047 R14).

Two endpoints, both proxying the Worker Executor's R14 app-health endpoints
via ``IWorkerRegistryClient``:

- ``GET /worker-apps``                       — per-app aggregate summary
- ``GET /worker-apps/{worker_app}/instances`` — per-instance detail rows

Both return a BARE JSON array — NOT the ``{status, data}`` envelope that
``ResponseWrapperMiddleware`` applies to the rest of ``/api/v1/*`` and
``/api/v2/*``. Same contract, same reason, and same fix as
``worker_replicas_controller`` (SA-2047 R10): the frontend types these
responses directly as ``T[]``. ``/api/v2/worker-apps`` is carved out of the
wrapper's ``ignore_paths`` in ``create_fastapi_app()`` right next to
``/api/v2/worker-replicas`` (see the v1 package ``__init__.py``).

Unlike ``worker_replicas_controller``'s per-type route, the instances route
here does NOT remap the executor's row shape (no ``_to_worker_instance``-style
translation) — Task 13's ``get_app_instances`` rows pass straight through.

Neither route is gated behind ``ENABLE_NODE_CATALOG_V2`` — that flag is
scoped to the node-catalog migration, not this feature.

Both client methods collapse ``None`` (executor unreachable, or a non-list
body — see ``IWorkerRegistryClient.get_app_summary``/``get_app_instances``)
into ``[]`` here. That ``None``/``[]`` distinction is load-bearing for the R9
realtime feed (Task 15: ``None`` -> hold the last snapshot, ``[]`` -> tombstone
every entry) but this is a plain snapshot HTTP read with no prior state to
hold onto, so both cases collapse to the same empty-array response.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request

from app.layer2_application.interfaces.worker_registry_client_interface import (
    IWorkerRegistryClient,
)

router = APIRouter(tags=["worker-apps"])


def _get_registry_client(request: Request) -> Optional[IWorkerRegistryClient]:
    """Same container key the v1 worker-function proxy uses
    (``worker_function_controller.py``'s ``_get_client``) — the CP wires
    exactly one ``HttpWorkerRegistryClient``/``GrpcWorkerRegistryClient``
    instance in ``bootstrap.py`` and exposes it under this key."""
    return request.app.state.container.get("_worker_registry_client")


@router.get("/worker-apps")
def list_worker_apps(
    client: Optional[IWorkerRegistryClient] = Depends(_get_registry_client),
) -> Any:
    if client is None:
        return []
    rows = client.get_app_summary()
    return rows if rows is not None else []


@router.get("/worker-apps/{worker_app}/instances")
def list_worker_app_instances(
    worker_app: str,
    client: Optional[IWorkerRegistryClient] = Depends(_get_registry_client),
) -> Any:
    if client is None:
        return []
    rows = client.get_app_instances(worker_app)
    return rows if rows is not None else []
