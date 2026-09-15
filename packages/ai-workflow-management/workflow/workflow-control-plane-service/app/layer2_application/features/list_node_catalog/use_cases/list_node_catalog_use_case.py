"""List the v2 node catalog for a tenant.

Aggregates summaries from the three NodeProvider implementations
(builtin, worker, business), builds a category tree, computes a stable
ETag, and caches the result per tenant.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.monitor_interface import IMonitor
from app.layer2_application.providers import (
    BuiltinNodeProvider,
    BusinessNodeProvider,
    WorkerNodeProvider,
)
from app.layer2_application.providers.category_metadata import (
    expand_with_parents,
    get_category_info,
)
from app.layer2_application.contracts import NodeCategoryInfo, NodeDefinitionSummary
from app.layer2_application.interfaces.node_catalog_cache_interface import (
    INodeCatalogCache,
)


@dataclass
class ListNodeCatalogCommand:
    tenant_id: str = "system"


@dataclass
class ListNodeCatalogResult:
    definitions: list[NodeDefinitionSummary] = field(default_factory=list)
    category_tree: dict[str, list[str]] = field(default_factory=dict)
    categories: dict[str, NodeCategoryInfo] = field(default_factory=dict)
    generated_at: str = ""
    etag: str = ""
    total: int = 0


def _compute_etag(summaries: list[NodeDefinitionSummary]) -> str:
    """Stable hash over the catalog content.

    Sort by id before hashing so provider ordering doesn't churn the ETag.
    ``spec_version`` is part of the per-entry dump, so any version bump
    invalidates automatically.
    """
    payload = [
        s.model_dump(by_alias=True)
        for s in sorted(summaries, key=lambda s: s.id)
    ]
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return 'W/"' + hashlib.sha256(body).hexdigest() + '"'


def _build_category_tree(summaries: list[NodeDefinitionSummary]) -> dict[str, list[str]]:
    tree: dict[str, list[str]] = {}
    for s in summaries:
        tree.setdefault(s.category, []).append(s.id)
    for ids in tree.values():
        ids.sort()
    return tree


def _build_categories(
    tree: dict[str, list[str]],
) -> dict[str, NodeCategoryInfo]:
    """Emit display metadata for each leaf category and its ancestor paths."""
    out: dict[str, NodeCategoryInfo] = {}
    for key in expand_with_parents(tree.keys()):
        label, order = get_category_info(key)
        out[key] = NodeCategoryInfo(label=label, order=order)
    return out


class ListNodeCatalogUseCase:
    """Feature: list_node_catalog."""

    def __init__(
        self,
        logger: ILogger,
        builtin_node_provider: BuiltinNodeProvider,
        worker_node_provider: WorkerNodeProvider,
        business_node_provider: BusinessNodeProvider,
        node_catalog_cache: INodeCatalogCache,
        monitor: Optional[IMonitor] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self._builtin = builtin_node_provider
        self._worker = worker_node_provider
        self._business = business_node_provider
        self._cache = node_catalog_cache
        self._monitor = monitor

    async def execute(
        self,
        command: Optional[ListNodeCatalogCommand] = None,
    ) -> ListNodeCatalogResult:
        command = command or ListNodeCatalogCommand()
        tenant_id = command.tenant_id or "system"
        start = time.perf_counter()

        cached = self._cache.get(tenant_id)
        if cached is not None:
            self._emit_metrics("cache_hit", start)
            return cached.result

        summaries: list[NodeDefinitionSummary] = []
        summaries.extend(self._builtin.list_summaries(tenant_id))
        summaries.extend(self._worker.list_summaries(tenant_id))
        summaries.extend(self._business.list_summaries(tenant_id))

        category_tree = _build_category_tree(summaries)
        result = ListNodeCatalogResult(
            definitions=summaries,
            category_tree=category_tree,
            categories=_build_categories(category_tree),
            generated_at=datetime.now(timezone.utc).isoformat(),
            etag=_compute_etag(summaries),
            total=len(summaries),
        )
        self._cache.put(tenant_id, result, result.etag)
        self._emit_metrics("cache_miss", start)
        return result

    def _emit_metrics(self, outcome: str, start: float) -> None:
        """Record the list call's outcome + duration.

        ``outcome`` is a bounded label (``cache_hit`` | ``cache_miss``).
        We deliberately do NOT tag by tenant_id — that would blow up
        cardinality in Prometheus. Tenant-level observation goes to logs.
        """
        if self._monitor is None:
            return
        elapsed = time.perf_counter() - start
        try:
            self._monitor.increment(
                "node_catalog_list_requests_total",
                tags={"outcome": outcome},
            )
            if outcome == "cache_hit":
                self._monitor.increment("node_catalog_list_cache_hits_total")
            self._monitor.histogram(
                "node_catalog_list_duration_seconds",
                elapsed,
                tags={"outcome": outcome},
            )
        except Exception as exc:
            self.logger.debug("metric emit failed", error=str(exc))
