"""Fetch multiple v2 node catalog details in a single call.

The frontend uses this on workflow load to hydrate every referenced node
in one round trip instead of N sequential detail calls. Capped at
``MAX_REFS_PER_BATCH`` to protect the executor from a flood.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer2_application.features.get_node_catalog_detail.use_cases.get_node_catalog_detail_use_case import (
    GetNodeCatalogDetailCommand,
    GetNodeCatalogDetailUseCase,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.monitor_interface import IMonitor
from app.layer2_application.contracts import NodeDefinitionDetail


MAX_REFS_PER_BATCH = 50


@dataclass
class NodeRef:
    node_id: str
    spec_version: Optional[str] = None


@dataclass
class GetNodeCatalogDetailsBatchCommand:
    refs: list[NodeRef] = field(default_factory=list)
    tenant_id: str = "system"


@dataclass
class GetNodeCatalogDetailsBatchResult:
    details: list[NodeDefinitionDetail] = field(default_factory=list)
    missing: list[NodeRef] = field(default_factory=list)
    etag: str = ""


def _compute_batch_etag(details: list[NodeDefinitionDetail]) -> str:
    payload = [d.model_dump(by_alias=True) for d in details]
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return 'W/"' + hashlib.sha256(body).hexdigest() + '"'


class BatchSizeExceededError(ValueError):
    """Raised when the caller requests more than MAX_REFS_PER_BATCH refs."""


class GetNodeCatalogDetailsBatchUseCase:
    """Feature: get_node_catalog_details_batch.

    The detail use case is assigned via post-wire in bootstrap (following
    the pattern used by ``edit_node_data._rerun_task``). Set to ``None``
    by default so smart-DI in bootstrap doesn't treat it as required and
    skip the feature.
    """

    def __init__(
        self,
        logger: ILogger,
        monitor: Optional[IMonitor] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self._detail_uc: Optional[GetNodeCatalogDetailUseCase] = None
        self._monitor = monitor

    async def execute(
        self,
        command: GetNodeCatalogDetailsBatchCommand,
    ) -> GetNodeCatalogDetailsBatchResult:
        if self._detail_uc is None:
            raise RuntimeError(
                "get_node_catalog_details_batch: detail use case not wired — "
                "bootstrap must assign it post-construction"
            )
        refs = list(command.refs or [])
        if len(refs) > MAX_REFS_PER_BATCH:
            raise BatchSizeExceededError(
                f"batch size {len(refs)} exceeds maximum {MAX_REFS_PER_BATCH}"
            )
        start = time.perf_counter()

        details: list[NodeDefinitionDetail] = []
        missing: list[NodeRef] = []
        for ref in refs:
            inner = await self._detail_uc.execute(
                GetNodeCatalogDetailCommand(
                    node_id=ref.node_id,
                    spec_version=ref.spec_version,
                    tenant_id=command.tenant_id,
                )
            )
            if inner.detail is None:
                missing.append(ref)
            else:
                details.append(inner.detail)

        self._emit_metrics(len(refs), len(missing), start)
        return GetNodeCatalogDetailsBatchResult(
            details=details,
            missing=missing,
            etag=_compute_batch_etag(details),
        )

    def _emit_metrics(self, requested: int, missing: int, start: float) -> None:
        """Batch-level rollup: count + duration + missing-ratio.

        Per-ref metrics are recorded by the underlying detail use case
        (one sample per ref). Don't double-count here — just the batch.
        """
        if self._monitor is None:
            return
        elapsed = time.perf_counter() - start
        try:
            self._monitor.increment("node_catalog_batch_requests_total")
            self._monitor.histogram(
                "node_catalog_batch_duration_seconds", elapsed,
            )
            # Bucketed size so the histogram key space stays bounded.
            if requested <= 5:
                size_bucket = "small"
            elif requested <= 20:
                size_bucket = "medium"
            else:
                size_bucket = "large"
            self._monitor.histogram(
                "node_catalog_batch_size", float(requested),
                tags={"size": size_bucket},
            )
            if missing > 0:
                self._monitor.increment(
                    "node_catalog_batch_missing_total",
                    tags={"size": size_bucket},
                )
        except Exception as exc:
            self.logger.debug("metric emit failed", error=str(exc))
