"""P0-2 acceptance (SA-2256): every budget bucket is observable, and every
whole-file download is counted and logged.

Gate G6 depends on ``full_materialize_total`` permanently. The ticket calls
this the most-skipped safeguard in this kind of work, and the failure mode is
specific: a resolver that silently falls back to a whole-file download on deep
non-index paths passes every functional test and then OOMs in production.
Nothing here asserts performance — it asserts that the numbers exist, are
fresh, and are labelled well enough to tell the P2-1 improvement apart from
the manifest cases it cannot fix.
"""

from __future__ import annotations

import gc
import logging

import pytest
from prometheus_client import generate_latest

from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.interfaces.cache_budget_registry import (
    CacheBudgetRegistry,
)
from app.layer4_frameworks.caching.bounded_ttl_cache import BoundedTTLCache
from app.layer4_frameworks.monitoring.cache_budget_collector import (
    CacheBudgetCollector,
    install_cache_budget_collector,
)
from app.layer4_frameworks.monitoring.prometheus import PrometheusMonitor
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.run_context.cached_projector_view_store import (
    CachedProjectorRunContextViewStore,
)


class _Bucket:
    def __init__(self, values: dict) -> None:
        self._values = values

    def budget_snapshot(self) -> dict:
        return self._values


class _BrokenBucket:
    def budget_snapshot(self) -> dict:
        raise RuntimeError("cannot read")


# ----------------------------------------------------------------------
# The registry
# ----------------------------------------------------------------------


class TestBudgetRegistry:
    def test_snapshot_collects_every_bucket(self):
        reg = CacheBudgetRegistry()
        a = _Bucket({"entries": 3})
        b = _Bucket({"entries": 5, "maxsize": 10})
        reg.register(a, "a")
        reg.register(b, "b")
        assert reg.snapshot() == {"a": {"entries": 3}, "b": {"entries": 5, "maxsize": 10}}

    def test_a_broken_bucket_does_not_break_the_scrape(self):
        """Observability must never be the thing that takes the pod down."""
        reg = CacheBudgetRegistry()
        good = _Bucket({"entries": 1})
        reg.register(_BrokenBucket(), "broken")
        reg.register(good, "good")
        assert reg.snapshot() == {"good": {"entries": 1}}

    def test_same_named_buckets_are_summed_not_overwritten(self):
        reg = CacheBudgetRegistry()
        one, two = _Bucket({"entries": 2}), _Bucket({"entries": 3})
        reg.register(one, "shared")
        reg.register(two, "shared")
        assert reg.snapshot()["shared"]["entries"] == 5

    def test_non_numeric_values_are_dropped(self):
        reg = CacheBudgetRegistry()
        b = _Bucket({"entries": 4, "name": "not-a-number", "flag": True})
        reg.register(b, "b")
        assert reg.snapshot()["b"] == {"entries": 4}

    def test_registry_does_not_pin_its_buckets(self):
        reg = CacheBudgetRegistry()
        reg.register(_Bucket({"entries": 1}), "temp")
        gc.collect()
        assert len(reg) == 0, "the budget registry became a leak of its own"

    def test_a_bucket_without_a_snapshot_is_rejected(self):
        reg = CacheBudgetRegistry()
        with pytest.raises(TypeError, match="budget_snapshot"):
            reg.register(object(), "x")

    def test_a_bucket_needs_a_name(self):
        reg = CacheBudgetRegistry()
        with pytest.raises(ValueError):
            reg.register(_Bucket({}), "")


# ----------------------------------------------------------------------
# The Prometheus export
# ----------------------------------------------------------------------


def _scrape(monitor) -> str:
    return generate_latest(monitor.registry).decode()


class TestPrometheusExport:
    def test_buckets_appear_on_the_existing_scrape(self):
        reg = CacheBudgetRegistry()
        cache = BoundedTTLCache(
            maxsize=8, ttl_seconds=60, name="metadata_stamping", budget_registry=reg,
        )
        cache.put("a", 1)
        cache.get("a")
        cache.get("missing")

        monitor = PrometheusMonitor()
        assert install_cache_budget_collector(monitor, reg) is not None
        body = _scrape(monitor)

        assert 'wcp_cache_entries{bucket="metadata_stamping"} 1.0' in body
        assert 'wcp_cache_maxsize{bucket="metadata_stamping"} 8.0' in body
        assert 'wcp_cache_hits{bucket="metadata_stamping"} 1.0' in body
        assert 'wcp_cache_misses{bucket="metadata_stamping"} 1.0' in body

    def test_values_are_read_at_scrape_time_not_at_registration(self):
        """A pushed gauge is stale between pushes; this must not be."""
        reg = CacheBudgetRegistry()
        cache = BoundedTTLCache(
            maxsize=100, ttl_seconds=0, name="live", budget_registry=reg,
        )
        monitor = PrometheusMonitor()
        install_cache_budget_collector(monitor, reg)

        assert 'wcp_cache_entries{bucket="live"} 0.0' in _scrape(monitor)
        for i in range(7):
            cache.put(i, i)
        assert 'wcp_cache_entries{bucket="live"} 7.0' in _scrape(monitor), (
            "the scrape returned a value captured earlier — a cache that grows "
            "between scrapes would be invisible"
        )

    def test_every_bucket_reports_entries_under_one_metric_name(self):
        """One family per FIELD labelled by bucket, not one family per bucket."""
        reg = CacheBudgetRegistry()
        # Strong references on purpose — membership is weak, so an unowned
        # bucket vanishes before the scrape. In the service the DI container
        # owns these.
        cache = BoundedTTLCache(
            maxsize=4, ttl_seconds=0, name="cache_a", budget_registry=reg,
        )
        store = CachedProjectorRunContextViewStore(
            MemoryEventLogRepository(), budget_registry=reg,
        )
        monitor = PrometheusMonitor()
        install_cache_budget_collector(monitor, reg)
        body = _scrape(monitor)
        assert 'wcp_cache_entries{bucket="cache_a"}' in body
        assert 'wcp_cache_entries{bucket="projector_view_store"}' in body, (
            "the projector reports its occupancy under a different metric name "
            "than every other bucket"
        )
        assert cache is not None and store is not None

    def test_a_registry_failure_yields_no_metrics_not_a_broken_scrape(self):
        class _Exploding:
            def snapshot(self):
                raise RuntimeError("boom")

        collector = CacheBudgetCollector(_Exploding())
        assert list(collector.collect()) == []

    def test_install_is_a_noop_without_a_prometheus_registry(self):
        class _StubMonitor:
            pass

        assert install_cache_budget_collector(_StubMonitor()) is None


# ----------------------------------------------------------------------
# full_materialize_total
# ----------------------------------------------------------------------


class _CountingMonitor:
    def __init__(self) -> None:
        self.counts: list[tuple[str, dict]] = []

    def track(self, metric, value):  # pragma: no cover - unused
        pass

    def increment(self, metric, tags=None):
        self.counts.append((metric, dict(tags or {})))

    def histogram(self, metric, value, tags=None):  # pragma: no cover - unused
        pass

    def causes(self) -> list[str]:
        return [t.get("cause") for m, t in self.counts if m == "full_materialize_total"]


class _StubFileClient:
    def __init__(self, payload: bytes = b"a,b\n1,2\n") -> None:
        self.payload = payload
        self.downloads: list[str] = []

    async def download_file(self, file_id: str):
        self.downloads.append(file_id)
        return self.payload


def _service(monitor=None, file_client=None) -> DataStoreService:
    from app.layer2_application.data.csv_converter import CsvConverter

    return DataStoreService(
        file_client or _StubFileClient(), CsvConverter(), monitor=monitor,
    )


class TestFullMaterializeSize:
    """``full_materialize_total`` says how OFTEN; nothing said how BIG.

    Without a size, "is flat_full_download expensive?" can only be guessed —
    it fired 67 times in 30 days and every one of them could have been 1 KB.
    """

    def test_zero_is_unknown_not_small(self):
        """A ref written before ``size_bytes`` was stamped declares 0.

        Folding that into ``<64K`` would read as "this materialize was tiny"
        when the truth is "nobody knows" — the opposite of what this counter
        exists to answer.
        """
        assert DataStoreService._size_bucket(0) == "unknown"
        assert DataStoreService._size_bucket(-1) == "unknown"
        assert DataStoreService._size_bucket(1) == "<64K"

    def test_boundaries_are_half_open(self):
        b = DataStoreService._size_bucket
        assert b(64 * 1024 - 1) == "<64K"
        assert b(64 * 1024) == "64K-1M"
        assert b(1024 * 1024 - 1) == "64K-1M"
        assert b(1024 * 1024) == "1M-8M"
        assert b(8 * 1024 * 1024) == "8M-64M"
        assert b(64 * 1024 * 1024) == "64M-256M"
        assert b(256 * 1024 * 1024) == ">256M"

    def test_cardinality_is_bounded(self):
        """No run_id, no file_id: 3 causes x 7 buckets is the whole surface."""
        labels = {DataStoreService._size_bucket(n) for n in
                  (0, 1, 10 ** 3, 10 ** 5, 10 ** 6, 10 ** 7, 10 ** 8, 10 ** 9, 10 ** 12)}
        assert labels <= {"unknown", "<64K", "64K-1M", "1M-8M", "8M-64M",
                          "64M-256M", ">256M"}
        assert len(labels) <= 7

    @pytest.mark.asyncio
    async def test_it_is_a_separate_metric_from_the_count(self):
        """Adding size_bucket to ``full_materialize_total`` would BREAK it.

        ``PrometheusMonitor._get_counter`` freezes a metric's label set on first
        observation and returns None — dropping the increment silently — for any
        later call with different keys. So the size must ride its own name.
        """
        monitor = _CountingMonitor()
        svc = _service(monitor)
        await svc.materialize(
            {"__file_ref": True, "file_id": "f-1", "size_bytes": 903955},
        )
        by_name = {m: t for m, t in monitor.counts}
        assert set(by_name["full_materialize_total"]) == {"cause"}, (
            "the existing counter grew a label — its frozen label set means "
            "every increment after the first is now dropped silently"
        )
        assert by_name["full_materialize_size_total"] == {
            "cause": "flat_full_download", "size_bucket": "64K-1M",
        }

    @pytest.mark.asyncio
    async def test_a_tree_reports_its_own_cause_with_a_size(self):
        monitor = _CountingMonitor()
        svc = _service(monitor)
        await svc.materialize({
            "__file_ref": True, "file_id": "f-1", "size_bytes": 20 * 1024 * 1024,
            "artifacts": [{"file_id": "c-1", "name": "rows",
                           "artifact_type": "nested_array"}],
        })
        sized = [t for m, t in monitor.counts if m == "full_materialize_size_total"]
        assert sized == [{"cause": "artifacts_not_row_rangeable",
                          "size_bucket": "8M-64M"}]

    @pytest.mark.asyncio
    async def test_an_empty_value_records_no_size(self):
        """Nothing is downloaded, so counting it would inflate both metrics."""
        monitor = _CountingMonitor()
        svc = _service(monitor)
        await svc.materialize({"__file_ref": True})
        assert monitor.counts == []


class TestSizeIsTheWholeTree:
    """The size label used the ROOT's ``size_bytes`` and nothing else.

    Measured live on tenant-1, 2026-08-21, run ``d93b7a38`` — a code node that
    returned 30 000 rows through an OUTPUT node:

        root                size_bytes =         23
        child "duplicates"  size_bytes = 10 775 163

    10.28 MiB was pulled into control-plane RAM and published as
    ``size_bucket="<64K"``. For ``artifacts_not_row_rangeable`` the root is
    always just the envelope, so the label was wrong on precisely the cause it
    exists to describe. ``probe_size_bytes`` had already learned this on the
    guard side ("it used to stop after the top-level artifacts ... reported 35%
    of the real bytes") and records that declared sizes cost no I/O at all.
    """

    def test_the_production_shape_is_not_reported_as_tiny(self):
        size = DataStoreService._declared_tree_bytes({
            "__file_ref": True, "file_id": "root", "size_bytes": 23,
            "artifacts": [{"file_id": "c-1", "name": "duplicates",
                           "artifact_type": "nested_array",
                           "size_bytes": 10_775_163}],
        })
        assert size == 10_775_186
        assert DataStoreService._size_bucket(size) == "8M-64M", (
            "the observed 10.28 MiB materialize is being bucketed as if it "
            "were the 23-byte envelope"
        )

    def test_it_recurses_all_the_way_down(self):
        """A recursive split nests; stopping at depth 1 undercounts again."""
        assert DataStoreService._declared_tree_bytes({
            "file_id": "r", "size_bytes": 1,
            "artifacts": [
                {"file_id": "a", "size_bytes": 10, "artifacts": [
                    {"file_id": "b", "size_bytes": 100, "artifacts": [
                        {"file_id": "c", "size_bytes": 1000},
                    ]},
                ]},
                {"file_id": "d", "size_bytes": 10000},
            ],
        }) == 11111

    def test_a_flat_ref_is_unchanged(self):
        """No artifacts means the root WAS the whole value; do not regress it."""
        assert DataStoreService._declared_tree_bytes(
            {"file_id": "f", "size_bytes": 903955}) == 903955

    def test_undeclared_children_make_it_a_floor_not_a_lie(self):
        """Never probe from a recorder: blocking here stalls the event loop,
        and on ``instances: 1`` that stalls the tenant. Missing sizes count 0,
        so the total is a floor — the honest direction to be wrong in."""
        assert DataStoreService._declared_tree_bytes({
            "file_id": "r", "size_bytes": 5,
            "artifacts": [{"file_id": "a"}, {"file_id": "b", "size_bytes": 7}],
        }) == 12

    def test_nothing_declared_anywhere_is_still_unknown(self):
        ref = {"file_id": "r", "artifacts": [{"file_id": "a"}]}
        assert DataStoreService._declared_tree_bytes(ref) == 0
        assert DataStoreService._size_bucket(0) == "unknown"

    def test_a_deep_tree_cannot_raise(self):
        """The ref comes back from a worker, so its depth is not ours to trust.
        A RecursionError inside a metrics path would take out the read."""
        node = {"file_id": "leaf", "size_bytes": 1}
        for _ in range(5000):
            node = {"file_id": "n", "size_bytes": 1, "artifacts": [node]}
        assert DataStoreService._declared_tree_bytes(node) > 0

    def test_garbage_in_artifacts_does_not_raise(self):
        assert DataStoreService._declared_tree_bytes({
            "file_id": "r", "size_bytes": 4,
            "artifacts": [None, "nope", 7, {"file_id": "a", "size_bytes": "x"},
                          {"file_id": "b", "size_bytes": 6}],
        }) == 10

    @pytest.mark.asyncio
    async def test_the_warn_threshold_now_sees_the_children(self, caplog):
        """A tree whose root is small but whose children are large is exactly
        the case the WARNING band exists for, and it used to log at INFO."""
        monitor = _CountingMonitor()
        svc = _service(monitor)
        with caplog.at_level(logging.INFO):
            await svc.materialize({
                "__file_ref": True, "file_id": "root", "size_bytes": 23,
                "artifacts": [{"file_id": "c-1", "name": "duplicates",
                               "artifact_type": "nested_array",
                               "size_bytes": 10_775_163}],
            })
        recs = [r for r in caplog.records if "FULL MATERIALIZE" in r.getMessage()]
        assert recs and recs[0].levelno == logging.WARNING
        sized = [t for m, t in monitor.counts
                 if m == "full_materialize_size_total"]
        assert sized == [{"cause": "artifacts_not_row_rangeable",
                          "size_bucket": "8M-64M"}]


class TestFullMaterializeCounter:
    @pytest.mark.asyncio
    async def test_a_flat_full_download_is_counted(self):
        monitor = _CountingMonitor()
        svc = _service(monitor)
        await svc.materialize({"__file_ref": True, "file_id": "f-1"})
        assert monitor.causes() == ["flat_full_download"], (
            "the whole-file download that P2-1 exists to eliminate is not "
            "counted — gate G6 has nothing to measure"
        )

    @pytest.mark.asyncio
    async def test_causes_are_distinguishable(self):
        """P2-1 should drive flat_full_download to ~0 and leave manifests."""
        monitor = _CountingMonitor()
        svc = _service(monitor)
        await svc.materialize({"__file_ref": True, "file_id": "f-1"})
        await svc.materialize({
            "__file_ref": True, "file_id": "f-2",
            "artifacts": [
                {"artifact_type": "nested_array", "file_id": "a-1", "name": "rows"},
            ],
        })
        await svc.materialize({
            "__file_ref": True,
            "artifacts": [
                {"artifact_type": "loop_chunk", "file_id": "c-1", "name": "chunk0"},
            ],
        })
        assert monitor.causes() == [
            "flat_full_download", "artifacts_not_row_rangeable", "loop_manifest",
        ]

    @pytest.mark.asyncio
    async def test_an_empty_value_is_not_counted(self):
        """No file, no download — counting it would inflate the G6 rate."""
        monitor = _CountingMonitor()
        svc = _service(monitor)
        await svc.materialize({"__file_ref": True, "file_id": ""})
        assert monitor.causes() == []

    @pytest.mark.asyncio
    async def test_a_non_file_ref_is_not_counted(self):
        monitor = _CountingMonitor()
        svc = _service(monitor)
        await svc.materialize({"plain": "dict"})
        assert monitor.causes() == []

    @pytest.mark.asyncio
    async def test_a_large_fallback_warns_with_the_actionable_fields(self, caplog):
        """Renamed from ``test_every_fallback_emits_a_warn_...`` 2026-08-21.

        It used ``size_bytes=4096`` and asserted WARNING. The warn band is now
        size-gated (see ``_FULL_MATERIALIZE_WARN_BYTES``) because a real
        production materialize was measured logging ``size_bytes=2547`` at
        WARNING, ~65 times per 30 days — noise that buries the 54 MB case that
        took the pod to 100% of its cgroup limit.

        What this test actually guards is the FIELD SET, and that is unchanged;
        only the size moved above the threshold so the warning path is still the
        one exercised. Small values still log every field, at INFO — see
        ``TestFullMaterializeLogLine``.
        """
        svc = _service(_CountingMonitor())
        with caplog.at_level(logging.WARNING):
            await svc.materialize(
                {"__file_ref": True, "file_id": "f-9", "size_bytes": 4194304},
                context="httpNode.body.items",
            )
        warns = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        line = next((w for w in warns if "FULL MATERIALIZE" in w), "")
        assert line, f"no FULL MATERIALIZE warning emitted; saw {warns}"
        assert "cause=flat_full_download" in line
        assert "node=httpNode" in line
        assert "expression=httpNode.body.items" in line
        assert "file_id=f-9" in line
        assert "size_bytes=4194304" in line

    @pytest.mark.asyncio
    async def test_an_unknown_size_says_unknown_rather_than_zero(self):
        svc = _service(_CountingMonitor())
        import logging as _logging
        records: list[str] = []

        class _Grab(_logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = _logging.getLogger(
            "app.layer2_application.data.data_store_service")
        handler = _Grab()
        logger.addHandler(handler)
        try:
            await svc.materialize({"__file_ref": True, "file_id": "f-1"})
        finally:
            logger.removeHandler(handler)
        line = next(r for r in records if "FULL MATERIALIZE" in r)
        assert "size_bytes=unknown" in line, (
            "an unmeasured value reported as 0 bytes reads as 'free'"
        )

    @pytest.mark.asyncio
    async def test_metrics_failure_never_breaks_the_read(self):
        class _BadMonitor:
            def increment(self, metric, tags=None):
                raise RuntimeError("metrics backend down")

        svc = _service(_BadMonitor())
        result = await svc.materialize({"__file_ref": True, "file_id": "f-1"})
        assert result is not None, "a metrics failure broke the data path"

    @pytest.mark.asyncio
    async def test_no_monitor_wired_is_still_safe(self):
        svc = _service(None)
        assert await svc.materialize({"__file_ref": True, "file_id": "f-1"}) is not None


class TestResolverBudgetReporting:
    def test_the_run_scoped_materialize_cache_reports_itself(self):
        """The ~320MB line P2-1 targets must be visible BEFORE P2-1 lands."""
        from app.layer2_application.resolve.file_backed_resolver import (
            FileBackedResolverStrategy,
        )

        reg = CacheBudgetRegistry()
        strategy = FileBackedResolverStrategy(_service(), budget_registry=reg)
        strategy._run_materialize_caches["run-1"] = {"f-1": {}, "f-2": {}}
        strategy._run_odata_caches["run-1"] = {("f-1", ()): {}}

        snap = reg.snapshot()["run_scoped_materialize"]
        assert snap["materialize_buckets"] == 1
        assert snap["materialize_entries"] == 2
        assert snap["odata_entries"] == 1

class TestFullMaterializeLogLine:
    """The log line is the only thing that names WHICH expression did it.

    Observed live 2026-08-21 before this fix — the same opaque id, three times,
    and the one useful field empty of meaning:

        cause=artifacts_not_row_rangeable node=file_01a0204d-...
        expression=file_01a0204d-... file_id=file_01a0204d-... size_bytes=2547
    """

    @pytest.mark.asyncio
    async def test_a_small_materialize_is_not_a_warning(self, caplog):
        """65 of these fire per 30 days at ~2.5 KB.

        An alert that shouts for 2.5 KB is one nobody reads when 54 MB arrives —
        and 54 MB is the size that took the pod to 100% of its cgroup limit.
        """
        monitor = _CountingMonitor()
        svc = _service(monitor)
        with caplog.at_level(logging.INFO):
            await svc.materialize(
                {"__file_ref": True, "file_id": "f-1", "size_bytes": 2547},
            )
        recs = [r for r in caplog.records if "FULL MATERIALIZE" in r.getMessage()]
        assert recs, "the event must still be logged, just not as a warning"
        assert recs[0].levelno == logging.INFO

    @pytest.mark.asyncio
    async def test_a_large_materialize_is_a_warning(self, caplog):
        monitor = _CountingMonitor()
        svc = _service(monitor)
        with caplog.at_level(logging.INFO):
            await svc.materialize(
                {"__file_ref": True, "file_id": "f-1", "size_bytes": 54_433_026},
            )
        recs = [r for r in caplog.records if "FULL MATERIALIZE" in r.getMessage()]
        assert recs and recs[0].levelno == logging.WARNING

    @pytest.mark.asyncio
    async def test_unknown_size_stays_loud(self, caplog):
        """A ref written before ``size_bytes`` was stamped declares 0.

        Silencing that as "it was small" is the one mistake this threshold must
        not make: the truth is nobody knows how big it was.
        """
        monitor = _CountingMonitor()
        svc = _service(monitor)
        with caplog.at_level(logging.INFO):
            await svc.materialize({"__file_ref": True, "file_id": "f-1"})
        recs = [r for r in caplog.records if "FULL MATERIALIZE" in r.getMessage()]
        assert recs and recs[0].levelno == logging.WARNING

    @pytest.mark.asyncio
    async def test_the_expression_reaches_the_log(self, caplog):
        """``expression=`` must be the path, not the file_id repeated."""
        monitor = _CountingMonitor()
        svc = _service(monitor)
        with caplog.at_level(logging.INFO):
            await svc.materialize(
                {"__file_ref": True, "file_id": "f-1", "size_bytes": 10},
                context="httpNode.body.items",
            )
        msg = [r.getMessage() for r in caplog.records
               if "FULL MATERIALIZE" in r.getMessage()][0]
        assert "expression=httpNode.body.items" in msg
        assert "node=httpNode" in msg, (
            "node is derived from the expression's first segment; if the "
            "expression is a file_id the node field is meaningless too"
        )


class TestResolverPassesTheExpression:
    """``_cached_materialize`` used to pass ``file_id`` as the context."""

    @pytest.mark.asyncio
    async def test_the_path_is_passed_as_context(self):
        from app.layer2_application.resolve.file_backed_resolver import (
            FileBackedResolverStrategy,
        )

        seen = {}

        class _Store:
            async def materialize_guarded(self, ref, *, max_bytes=None, context=""):
                seen["context"] = context
                return {"ok": True}

        strat = FileBackedResolverStrategy.__new__(FileBackedResolverStrategy)
        strat._store = _Store()
        strat._get_materialize_cache = lambda: {}
        strat._remember_materialized = lambda *a, **k: None
        await strat._cached_materialize({"file_id": "f-9"}, context="orders.lines")
        assert seen["context"] == "orders.lines"

    @pytest.mark.asyncio
    async def test_it_falls_back_to_file_id(self):
        """A caller with no path must not make the log emptier than before."""
        from app.layer2_application.resolve.file_backed_resolver import (
            FileBackedResolverStrategy,
        )

        seen = {}

        class _Store:
            async def materialize_guarded(self, ref, *, max_bytes=None, context=""):
                seen["context"] = context
                return {}

        strat = FileBackedResolverStrategy.__new__(FileBackedResolverStrategy)
        strat._store = _Store()
        strat._get_materialize_cache = lambda: {}
        strat._remember_materialized = lambda *a, **k: None
        await strat._cached_materialize({"file_id": "f-9"})
        assert seen["context"] == "f-9"

