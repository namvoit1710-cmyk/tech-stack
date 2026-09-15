"""Fetch a single v2 node catalog entry with its full FormField schemas.

Dispatches by ``node_id`` prefix (``builtin:`` / ``worker:`` / ``business:``)
to the corresponding NodeProvider. Returns ``None`` for unknown refs —
the controller layer translates to 404.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.monitor_interface import IMonitor
from app.layer2_application.providers import (
    BuiltinNodeProvider,
    BusinessNodeProvider,
    WorkerNodeProvider,
)
from app.layer2_application.contracts import NodeDefinitionDetail


@dataclass
class GetNodeCatalogDetailCommand:
    node_id: str = ""
    spec_version: Optional[str] = None
    tenant_id: str = "system"


@dataclass
class GetNodeCatalogDetailResult:
    detail: Optional[NodeDefinitionDetail] = None
    etag: str = ""


def _compute_etag(detail: NodeDefinitionDetail) -> str:
    body = json.dumps(
        detail.model_dump(by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return 'W/"' + hashlib.sha256(body).hexdigest() + '"'


class GetNodeCatalogDetailUseCase:
    """Feature: get_node_catalog_detail."""

    def __init__(
        self,
        logger: ILogger,
        builtin_node_provider: BuiltinNodeProvider,
        worker_node_provider: WorkerNodeProvider,
        business_node_provider: BusinessNodeProvider,
        monitor: Optional[IMonitor] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self._providers = {
            "builtin:": builtin_node_provider,
            "worker:": worker_node_provider,
            "business:": business_node_provider,
        }
        self._monitor = monitor

    async def execute(
        self,
        command: GetNodeCatalogDetailCommand,
    ) -> GetNodeCatalogDetailResult:
        start = time.perf_counter()
        node_id = command.node_id or ""
        if not node_id:
            self._emit_metrics("unknown", "bad_request", start)
            return GetNodeCatalogDetailResult(detail=None)

        provider_prefix = _matching_prefix(node_id, self._providers)
        provider = self._providers.get(provider_prefix) if provider_prefix else None
        if provider is None:
            self._emit_metrics("unknown", "bad_request", start)
            return GetNodeCatalogDetailResult(detail=None)

        provider_label = provider_prefix.rstrip(":") if provider_prefix else "unknown"
        detail = provider.get_detail(node_id, command.spec_version)
        if detail is None:
            self._emit_metrics(provider_label, "not_found", start)
            return GetNodeCatalogDetailResult(detail=None)

        self._emit_metrics(provider_label, "ok", start)
        return GetNodeCatalogDetailResult(
            detail=detail,
            etag=_compute_etag(detail),
        )

    def _emit_metrics(self, provider: str, status: str, start: float) -> None:
        """Record one detail-call's provider + status + duration.

        Labels are bounded: ``provider`` ∈ {builtin, worker, business, unknown};
        ``status`` ∈ {ok, not_found, bad_request}. Specifically no ``id``
        or ``version`` labels — those would explode Prometheus cardinality.
        """
        if self._monitor is None:
            return
        elapsed = time.perf_counter() - start
        try:
            self._monitor.increment(
                "node_catalog_detail_requests_total",
                tags={"provider": provider, "status": status},
            )
            self._monitor.histogram(
                "node_catalog_detail_duration_seconds",
                elapsed,
                tags={"provider": provider, "status": status},
            )
        except Exception as exc:
            self.logger.debug("metric emit failed", error=str(exc))


def _matching_prefix(node_id: str, providers: dict) -> Optional[str]:
    for prefix in providers:
        if node_id.startswith(prefix):
            return prefix
    return None
