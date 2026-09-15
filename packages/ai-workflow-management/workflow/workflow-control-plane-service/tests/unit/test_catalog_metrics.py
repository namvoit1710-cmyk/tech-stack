"""Tests for Phase 3 catalog metrics emission.

Fake IMonitor captures every metric call. Each test asserts the right
bounded-cardinality labels are emitted without checking exact values —
the goal is to verify the emission plumbing, not the metric backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from app.layer2_application.features.get_node_catalog_detail.use_cases.get_node_catalog_detail_use_case import (
    GetNodeCatalogDetailCommand,
    GetNodeCatalogDetailUseCase,
)
from app.layer2_application.features.get_node_catalog_details_batch.use_cases.get_node_catalog_details_batch_use_case import (
    GetNodeCatalogDetailsBatchCommand,
    GetNodeCatalogDetailsBatchUseCase,
    NodeRef,
)
from app.layer2_application.features.list_node_catalog import ListNodeCatalogUseCase
from app.layer2_application.features.list_node_catalog.use_cases.list_node_catalog_use_case import (
    ListNodeCatalogCommand,
)
from app.layer2_application.contracts import (
    NodeDefinitionDetail,
    NodeDefinitionSummary,
    NodeRuntime,
    NodeVisibility,
)
from app.layer4_frameworks.caching import NodeCatalogCache


@dataclass
class _CapturingMonitor:
    counters: list[tuple[str, dict]] = field(default_factory=list)
    histograms: list[tuple[str, float, dict]] = field(default_factory=list)
    gauges: list[tuple[str, float]] = field(default_factory=list)

    def increment(self, metric: str, tags: Optional[dict] = None) -> None:
        self.counters.append((metric, tags or {}))

    def histogram(self, metric: str, value: float, tags: Optional[dict] = None) -> None:
        self.histograms.append((metric, value, tags or {}))

    def track(self, metric: str, value: float) -> None:
        self.gauges.append((metric, value))


class _DummyLogger:
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


def _summary(id_: str, category: str = "cat") -> NodeDefinitionSummary:
    return NodeDefinitionSummary(
        id=id_, name=id_, description="",
        provider="builtin", node_type="TASK", category=category,
        icon="Cog", color="#000", tags=[], spec_version="1.0.0",
        visibility=NodeVisibility.VISIBLE, runtime=NodeRuntime.ENGINE,
    )


def _detail(id_: str) -> NodeDefinitionDetail:
    return NodeDefinitionDetail(
        id=id_, name=id_, description="",
        provider="builtin", node_type="TASK", category="cat",
        icon="Cog", color="#000", tags=[], spec_version="1.0.0",
        visibility=NodeVisibility.VISIBLE, runtime=NodeRuntime.ENGINE,
    )


class _FakeProvider:
    def __init__(self, summaries=None, detail_map=None):
        self._summaries = summaries or []
        self._detail_map = detail_map or {}

    def list_summaries(self, tenant_id="system"):
        return list(self._summaries)

    def get_detail(self, node_id, spec_version=None):
        return self._detail_map.get(node_id)


# ---------------------------------------------------------------------------
# List use case metrics
# ---------------------------------------------------------------------------


class TestListMetrics:
    def _make(self, monitor):
        return ListNodeCatalogUseCase(
            logger=_DummyLogger(),
            builtin_node_provider=_FakeProvider(summaries=[_summary("builtin:a")]),
            worker_node_provider=_FakeProvider(),
            business_node_provider=_FakeProvider(),
            node_catalog_cache=NodeCatalogCache(),
            monitor=monitor,
        )

    @pytest.mark.asyncio
    async def test_cache_miss_emits_miss_metric(self):
        m = _CapturingMonitor()
        uc = self._make(m)
        await uc.execute(ListNodeCatalogCommand())
        names = [name for name, _ in m.counters]
        assert "node_catalog_list_requests_total" in names
        outcome_tags = [tags for name, tags in m.counters if name == "node_catalog_list_requests_total"]
        assert outcome_tags[0]["outcome"] == "cache_miss"

    @pytest.mark.asyncio
    async def test_cache_hit_on_second_call(self):
        m = _CapturingMonitor()
        uc = self._make(m)
        await uc.execute(ListNodeCatalogCommand())
        m.counters.clear()
        await uc.execute(ListNodeCatalogCommand())
        outcomes = {
            tags["outcome"]
            for name, tags in m.counters
            if name == "node_catalog_list_requests_total"
        }
        assert outcomes == {"cache_hit"}
        assert ("node_catalog_list_cache_hits_total", {}) in m.counters

    @pytest.mark.asyncio
    async def test_duration_histogram_recorded(self):
        m = _CapturingMonitor()
        uc = self._make(m)
        await uc.execute(ListNodeCatalogCommand())
        histos = [h for h in m.histograms if h[0] == "node_catalog_list_duration_seconds"]
        assert len(histos) == 1
        assert histos[0][1] >= 0.0  # elapsed seconds

    @pytest.mark.asyncio
    async def test_no_monitor_is_safe(self):
        uc = ListNodeCatalogUseCase(
            logger=_DummyLogger(),
            builtin_node_provider=_FakeProvider(summaries=[_summary("x")]),
            worker_node_provider=_FakeProvider(),
            business_node_provider=_FakeProvider(),
            node_catalog_cache=NodeCatalogCache(),
            monitor=None,
        )
        r = await uc.execute(ListNodeCatalogCommand())
        assert r.total == 1


# ---------------------------------------------------------------------------
# Detail use case metrics
# ---------------------------------------------------------------------------


class TestDetailMetrics:
    def _make(self, monitor, builtin_map=None):
        return GetNodeCatalogDetailUseCase(
            logger=_DummyLogger(),
            builtin_node_provider=_FakeProvider(detail_map=builtin_map or {}),
            worker_node_provider=_FakeProvider(),
            business_node_provider=_FakeProvider(),
            monitor=monitor,
        )

    @pytest.mark.asyncio
    async def test_ok_status_tagged(self):
        m = _CapturingMonitor()
        uc = self._make(m, builtin_map={"builtin:a": _detail("builtin:a")})
        await uc.execute(GetNodeCatalogDetailCommand(node_id="builtin:a"))
        tags = [t for n, t in m.counters if n == "node_catalog_detail_requests_total"][0]
        assert tags == {"provider": "builtin", "status": "ok"}

    @pytest.mark.asyncio
    async def test_not_found_status_tagged(self):
        m = _CapturingMonitor()
        uc = self._make(m)
        await uc.execute(GetNodeCatalogDetailCommand(node_id="builtin:nope"))
        tags = [t for n, t in m.counters if n == "node_catalog_detail_requests_total"][0]
        assert tags == {"provider": "builtin", "status": "not_found"}

    @pytest.mark.asyncio
    async def test_unknown_prefix_status_tagged(self):
        m = _CapturingMonitor()
        uc = self._make(m)
        await uc.execute(GetNodeCatalogDetailCommand(node_id="nonsense:x"))
        tags = [t for n, t in m.counters if n == "node_catalog_detail_requests_total"][0]
        assert tags == {"provider": "unknown", "status": "bad_request"}

    @pytest.mark.asyncio
    async def test_no_id_or_version_in_labels(self):
        """High-cardinality guardrail: neither id nor version should leak into tags."""
        m = _CapturingMonitor()
        uc = self._make(m, builtin_map={"builtin:a": _detail("builtin:a")})
        await uc.execute(GetNodeCatalogDetailCommand(node_id="builtin:a", spec_version="1.0.0"))
        for name, tags in m.counters:
            assert "id" not in tags
            assert "node_id" not in tags
            assert "version" not in tags
            assert "spec_version" not in tags


# ---------------------------------------------------------------------------
# Batch use case metrics
# ---------------------------------------------------------------------------


class _FakeDetailUC:
    def __init__(self, store):
        self._store = store

    async def execute(self, command):
        from app.layer2_application.features.get_node_catalog_detail.use_cases.get_node_catalog_detail_use_case import (
            GetNodeCatalogDetailResult,
        )
        return GetNodeCatalogDetailResult(detail=self._store.get(command.node_id))


class TestBatchMetrics:
    @pytest.mark.asyncio
    async def test_batch_request_counted(self):
        m = _CapturingMonitor()
        uc = GetNodeCatalogDetailsBatchUseCase(logger=_DummyLogger(), monitor=m)
        uc._detail_uc = _FakeDetailUC({"builtin:a": _detail("builtin:a")})
        await uc.execute(GetNodeCatalogDetailsBatchCommand(refs=[NodeRef("builtin:a")]))
        assert ("node_catalog_batch_requests_total", {}) in m.counters

    @pytest.mark.asyncio
    async def test_batch_size_bucket(self):
        m = _CapturingMonitor()
        uc = GetNodeCatalogDetailsBatchUseCase(logger=_DummyLogger(), monitor=m)
        uc._detail_uc = _FakeDetailUC({})
        refs = [NodeRef(f"builtin:n{i}") for i in range(10)]
        await uc.execute(GetNodeCatalogDetailsBatchCommand(refs=refs))
        size_tags = [
            tags for name, _, tags in m.histograms if name == "node_catalog_batch_size"
        ]
        assert size_tags[0]["size"] == "medium"  # 6-20 → medium

    @pytest.mark.asyncio
    async def test_missing_triggers_counter(self):
        m = _CapturingMonitor()
        uc = GetNodeCatalogDetailsBatchUseCase(logger=_DummyLogger(), monitor=m)
        uc._detail_uc = _FakeDetailUC({})  # nothing stored → all missing
        await uc.execute(GetNodeCatalogDetailsBatchCommand(refs=[NodeRef("builtin:a")]))
        names = [name for name, _ in m.counters]
        assert "node_catalog_batch_missing_total" in names


# ---------------------------------------------------------------------------
# Legacy palette counter
# ---------------------------------------------------------------------------


class TestLegacyCounter:
    @pytest.mark.asyncio
    async def test_legacy_call_increments_counter(self):
        from app.layer2_application.features.list_node_palette.use_cases.list_node_palette_use_case import (
            ListNodePaletteCommand,
            ListNodePaletteUseCase,
        )
        m = _CapturingMonitor()
        uc = ListNodePaletteUseCase(logger=_DummyLogger(), monitor=m)
        await uc.execute(ListNodePaletteCommand())
        assert ("node_palette_legacy_requests_total", {}) in m.counters
