"""gRPC client for querying the worker registry on the Executor service.

Drop-in replacement for ``HttpWorkerRegistryClient``.
Implements ``IWorkerRegistryClient`` via gRPC stubs.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import uuid4

import grpc
import httpx
from google.protobuf.json_format import MessageToDict

from workflow_proto.v1 import worker_registry_pb2 as pb
from workflow_proto.v1 import worker_registry_pb2_grpc as pb_grpc

from app.layer1_domain.value_objects.node_kind import NodeKind
from app.layer2_application.interfaces.worker_registry_client_interface import IWorkerRegistryClient

logger = logging.getLogger(__name__)


def _worker_detail_to_dict(w: Any) -> dict[str, Any]:
    d = MessageToDict(w, preserving_proto_field_name=True)
    # Ensure worker_id key exists (frontend expects it)
    d.setdefault("worker_id", w.worker_id)
    # Functional taxonomy (SA-1734). MessageToDict drops default-valued
    # scalars, so carry `kind` explicitly and normalize absent => "action"
    # (mirrors the executor-side contract) so the catalog/palette can rely
    # on the key always being present.
    #
    # This client is a SECOND reader of the same wire the executor already
    # validates at registration (normalize_kind rejects an unknown kind at
    # Register-time) — but a worker registered before that guard existed, or
    # a future bypass, can still hand this client anything. Coerce rather than
    # raise: an unknown/blank kind must not 500 the whole catalog for every
    # tenant just because one worker sent junk. Log loud enough to be caught.
    raw_kind = (getattr(w, "kind", "") or "").strip()
    if not raw_kind:
        kind = NodeKind.ACTION
    else:
        try:
            kind = NodeKind(raw_kind)
        except ValueError:
            logger.warning(
                "worker registry: worker_type=%r sent unknown kind=%r off the "
                "gRPC wire; coercing to %r so the catalog still renders",
                d.get("worker_type") or d.get("worker_id", "?"),
                raw_kind,
                NodeKind.ACTION.value,
            )
            kind = NodeKind.ACTION
    # Plain str on this dict deliberately (it's a wire-shaped dict consumed by
    # providers that f-string it into `category`), not the NodeKind member.
    d["kind"] = kind.value
    return d


class GrpcWorkerRegistryClient(IWorkerRegistryClient):
    """Queries the ``WorkerRegistryService`` on the Executor via gRPC."""

    def __init__(
        self,
        grpc_target: str,
        http_fallback_url: Optional[str] = None,
    ) -> None:
        self._target = grpc_target
        self._http_fallback = None
        if http_fallback_url:
            from app.layer4_frameworks.clients.http_worker_registry_client import (
                HttpWorkerRegistryClient,
            )
            self._http_fallback = HttpWorkerRegistryClient(http_fallback_url)

    def _stub(self) -> pb_grpc.WorkerRegistryServiceStub:
        channel = grpc.insecure_channel(self._target)
        return pb_grpc.WorkerRegistryServiceStub(channel)

    def list_workers(self) -> list[dict[str, Any]]:
        try:
            resp = self._stub().ListWorkers(pb.ListWorkersRequest(), timeout=30.0)
            return [_worker_detail_to_dict(w) for w in resp.workers]
        except Exception as exc:
            logger.error("gRPC list_workers failed: %s", exc)
            if self._http_fallback is not None:
                logger.info("Falling back to HTTP for list_workers")
                return self._http_fallback.list_workers()
            return []

    def get_worker(self, worker_id: str) -> Optional[dict[str, Any]]:
        try:
            resp = self._stub().GetWorker(pb.GetWorkerRequest(worker_id=worker_id), timeout=30.0)
            return _worker_detail_to_dict(resp)
        except Exception as exc:
            logger.error("gRPC get_worker failed: %s", exc)
            if self._http_fallback is not None:
                logger.info("Falling back to HTTP for get_worker")
                return self._http_fallback.get_worker(worker_id)
            return None

    def get_worker_by_type(self, worker_type: str) -> Optional[dict[str, Any]]:
        if not worker_type:
            return None
        for w in self.list_workers():
            if w.get("worker_type") == worker_type:
                return w
        return None

    def get_replica_summary(self) -> Optional[list[dict[str, Any]]]:
        """R6's per-type replica aggregate (GET /workers/replicas) is a REST-only
        endpoint on the Executor — there is no ``WorkerRegistryService`` RPC for
        it, and SA-2056/R9 does not add one (R6 is REST, R9 is pure control-plane;
        extending the proto is a separate ticket). There is therefore no gRPC
        call to even attempt here, unlike ``list_workers``/``get_worker``/
        ``get_worker_schema`` above, which try gRPC first and fall back to
        ``self._http_fallback`` only on failure — this method goes straight to
        the fallback, in the same shape those methods use for their fallback
        branch.

        ``self._http_fallback`` (an ``HttpWorkerRegistryClient``) is populated
        from ``WORKER_EXECUTION_URL`` under ``TRANSPORT_MODE=grpc`` — see the
        gRPC branch of ``create_messaging()`` in ``messaging/__init__.py`` —
        so a normally-configured gRPC-transport CP answers this correctly via
        HTTP.

        Preserves the tri-state contract ``IWorkerRegistryClient`` defines for
        this call end to end: ``None`` (cannot determine — either the fallback
        itself is unreachable, or there is no fallback configured at all),
        ``[]`` (genuine empty registry), or the rows. R9's differ guards on
        ``if rows is None: return`` and must never see ``None`` collapsed
        into a tombstoning ``[]``, or vice versa.
        """
        if self._http_fallback is not None:
            return self._http_fallback.get_replica_summary()
        logger.warning(
            "get_replica_summary: no HTTP fallback configured (WORKER_EXECUTION_URL "
            "unset?) — gRPC has no replica-aggregate RPC of its own (R6's "
            "/workers/replicas is REST-only), so R9's replica feed cannot "
            "determine worker health on this transport until a fallback URL "
            "is configured",
        )
        return None

    def get_type_replicas(self, worker_type: str) -> Optional[list[dict[str, Any]]]:
        """Same rationale as ``get_replica_summary`` above — R6's per-instance
        detail endpoint (SA-2047 R10) is REST-only; there is no
        ``WorkerRegistryService`` RPC for it, so there is no gRPC call to
        attempt. Goes straight to ``self._http_fallback``, same shape as
        ``get_replica_summary``'s fallback branch."""
        if self._http_fallback is not None:
            return self._http_fallback.get_type_replicas(worker_type)
        logger.warning(
            "get_type_replicas(%s): no HTTP fallback configured (WORKER_EXECUTION_URL "
            "unset?) — gRPC has no per-instance replica RPC of its own (R6's "
            "/workers/type/{type}/replicas is REST-only)",
            worker_type,
        )
        return None

    def get_worker_schema(self, worker_id: str) -> Optional[dict[str, Any]]:
        try:
            resp = self._stub().GetWorkerSchema(pb.GetWorkerSchemaRequest(worker_id=worker_id), timeout=30.0)
            return MessageToDict(resp, preserving_proto_field_name=True)
        except Exception as exc:
            logger.error("gRPC get_worker_schema failed: %s", exc)
            if self._http_fallback is not None:
                logger.info("Falling back to HTTP for get_worker_schema")
                return self._http_fallback.get_worker_schema(worker_id)
            return None

    def discover_workers(self, capability: str) -> list[dict[str, Any]]:
        try:
            resp = self._stub().DiscoverWorkers(
                pb.DiscoverWorkersRequest(capability=capability), timeout=30.0,
            )
            return [_worker_detail_to_dict(w) for w in resp.workers]
        except Exception as exc:
            logger.error("gRPC discover_workers failed: %s", exc)
            if self._http_fallback is not None:
                logger.info("Falling back to HTTP for discover_workers")
                return self._http_fallback.discover_workers(capability)
            return []

    def list_worker_functions(self, worker_type: str) -> list[dict[str, Any]]:
        """List functions from WorkerDetail.functions field."""
        try:
            # Use ListWorkers and find by worker_type, then extract functions
            resp = self._stub().ListWorkers(pb.ListWorkersRequest(), timeout=30.0)
            for w in resp.workers:
                if w.worker_type == worker_type:
                    return [
                        {
                            "name": f.name,
                            "description": f.description,
                            "input_schema": MessageToDict(f.input_schema) if f.HasField("input_schema") else {},
                            "output_schema": MessageToDict(f.output_schema) if f.HasField("output_schema") else {},
                        }
                        for f in w.functions
                    ]
            return []
        except Exception as exc:
            logger.error("gRPC list_worker_functions failed: %s", exc)
            if self._http_fallback is not None:
                logger.info("Falling back to HTTP for list_worker_functions")
                return self._http_fallback.list_worker_functions(worker_type)
            return []

    def invoke_worker_function(self, worker_type: str, function_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Invoke a worker function via the executor's REST API (gRPC RPC not yet exposed on executor)."""
        if self._http_fallback is not None:
            return self._http_fallback.invoke_worker_function(worker_type, function_name, params)
        try:
            host = self._target.split(":")[0]
            url = f"http://{host}:8004/api/v1/workers/type/{worker_type}/functions/{function_name}/invoke"
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, json={"params": params})
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and "data" in data:
                    data = data["data"]
                return data
        except Exception as exc:
            logger.error("invoke_worker_function failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    def _resolve_worker_endpoint(self, worker_type: str) -> Optional[str]:
        for w in self.list_workers():
            if w.get("worker_type") != worker_type:
                continue
            endpoint = w.get("endpoint_url") or w.get("endpoint")
            if endpoint:
                return endpoint.rstrip("/")
        return None

    def execute_task(
        self,
        worker_type: str,
        input_data: dict[str, Any],
        node_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke a worker synchronously by calling its /api/v1/execute endpoint directly."""
        endpoint = self._resolve_worker_endpoint(worker_type)
        if not endpoint:
            return {"status": "FAILED", "error": f"No worker found for type '{worker_type}'"}

        url = f"{endpoint}/api/v1/execute"
        payload = {
            "task_id": f"test-{uuid4().hex[:8]}",
            "action": "execute",
            "inputs": input_data,
            "parameters": node_config or {},
        }
        try:
            logger.info("execution payload: %s", payload)
            with httpx.Client(timeout=120.0) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and "data" in data:
                    data = data["data"]
                return data
        except Exception as exc:
            logger.error("execute_task failed (worker_type=%s, url=%s): %s", worker_type, url, exc)
            return {"status": "FAILED", "error": str(exc)}
