"""SA-2262 (part 1): the run-scoped materialize cache now has a ceiling.

This cache is the budget's largest line — the app's own config comment sizes it
at ~80MB per run × ORCHESTRATION_WORKER_COUNT, which is more than the whole
256MB target before anything else is counted. It was unbounded: every distinct
file a run touched stayed resident until that run reached a terminal state, and
concurrent runs each got their own bucket.

Correctness is unchanged and is covered by the parity suite
(``test_resolution_parity_suite.py``); these tests are only about the ceiling
holding and the bytes being reclaimed.
"""

from __future__ import annotations

import pytest

from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve import file_backed_resolver as fbr
from app.layer2_application.resolve.file_backed_resolver import (
    FileBackedResolverStrategy,
)
from app.layer2_application.resolve.variable_resolver import (
    reset_current_run_id,
    set_current_run_id,
)
from app.layer4_frameworks.clients.mock_file_service_client import (
    MockFileServiceClient,
)


@pytest.fixture
def strategy():
    client = MockFileServiceClient()
    store = DataStoreService(client, CsvConverter())
    return FileBackedResolverStrategy(store, file_client=None)


@pytest.fixture
def run_id():
    token = set_current_run_id("cap-run")
    try:
        yield "cap-run"
    finally:
        reset_current_run_id(token)


def _ref(file_id: str, size_bytes: int) -> dict:
    return {
        "__file_ref": True,
        "file_id": file_id,
        "size_bytes": size_bytes,
        "row_count": 1,
        "columns": ["a"],
    }


class TestTheCeilingHolds:
    def test_cache_bytes_stay_under_the_cap(self, strategy, run_id, monkeypatch):
        monkeypatch.setattr(fbr, "_MATERIALIZE_CACHE_MAX_BYTES", 1_000_000)
        monkeypatch.setattr(fbr, "_MATERIALIZE_PARSE_AMPLIFICATION", 1)
        bucket = strategy._get_materialize_cache()

        for i in range(200):
            strategy._remember_materialized(
                bucket, f"f-{i}", {"row": i}, _ref(f"f-{i}", 100_000),
            )

        snap = strategy.budget_snapshot()
        assert snap["materialize_bytes_estimate"] <= 1_000_000, (
            f"{snap['materialize_bytes_estimate']} bytes retained against a "
            "1MB cap — the ceiling is not holding"
        )
        assert snap["materialize_evictions"] > 0
        assert len(bucket) <= 10

    def test_eviction_is_oldest_first(self, strategy, run_id, monkeypatch):
        monkeypatch.setattr(fbr, "_MATERIALIZE_CACHE_MAX_BYTES", 300)
        monkeypatch.setattr(fbr, "_MATERIALIZE_PARSE_AMPLIFICATION", 1)
        bucket = strategy._get_materialize_cache()
        for name in ("a", "b", "c", "d"):
            strategy._remember_materialized(bucket, name, name, _ref(name, 100))
        assert "a" not in bucket, "the oldest entry survived the cap"
        assert "d" in bucket, "the newest entry was evicted"

    def test_an_unmeasurable_ref_fails_closed(self, strategy, run_id, monkeypatch):
        """No declared size must not read as 'free'."""
        monkeypatch.setattr(fbr, "_MATERIALIZE_CACHE_MAX_BYTES", 1_000)
        monkeypatch.setattr(fbr, "_MATERIALIZE_UNKNOWN_CHARGE", 1_000)
        bucket = strategy._get_materialize_cache()
        strategy._remember_materialized(
            bucket, "unsized", {"big": "value"}, {"__file_ref": True, "file_id": "unsized"},
        )
        strategy._remember_materialized(
            bucket, "second", {"x": 1}, _ref("second", 1),
        )
        assert "unsized" not in bucket, (
            "a ref with no declared size was charged nothing, so it could sit "
            "in the cache forever"
        )

    def test_cap_of_zero_disables_the_ceiling(self, strategy, run_id, monkeypatch):
        """An escape hatch that needs no rollback."""
        monkeypatch.setattr(fbr, "_MATERIALIZE_CACHE_MAX_BYTES", 0)
        bucket = strategy._get_materialize_cache()
        for i in range(50):
            strategy._remember_materialized(
                bucket, f"f-{i}", i, _ref(f"f-{i}", 10_000_000),
            )
        assert len(bucket) == 50
        assert strategy.budget_snapshot()["materialize_evictions"] == 0


class TestBytesAreReclaimed:
    def test_terminal_run_releases_its_bytes(self, strategy, monkeypatch):
        monkeypatch.setattr(fbr, "_MATERIALIZE_CACHE_MAX_BYTES", 10_000_000)
        monkeypatch.setattr(fbr, "_MATERIALIZE_PARSE_AMPLIFICATION", 1)
        token = set_current_run_id("run-a")
        try:
            bucket = strategy._get_materialize_cache()
            for i in range(5):
                strategy._remember_materialized(
                    bucket, f"f-{i}", i, _ref(f"f-{i}", 1_000),
                )
            assert strategy.budget_snapshot()["materialize_bytes_estimate"] == 5_000
        finally:
            reset_current_run_id(token)

        strategy.drop_run_cache("run-a")
        snap = strategy.budget_snapshot()
        assert snap["materialize_bytes_estimate"] == 0, (
            "a terminal run dropped its entries but kept its byte charge — the "
            "accounting drifts up until nothing can be cached at all"
        )
        assert snap["materialize_entries"] == 0

    def test_byte_accounting_does_not_drift_across_many_runs(
        self, strategy, monkeypatch,
    ):
        """The failure this guards is silent: a leaked charge shrinks the
        usable cache each cycle until every read misses."""
        monkeypatch.setattr(fbr, "_MATERIALIZE_CACHE_MAX_BYTES", 10_000_000)
        monkeypatch.setattr(fbr, "_MATERIALIZE_PARSE_AMPLIFICATION", 1)
        for n in range(30):
            token = set_current_run_id(f"run-{n}")
            try:
                bucket = strategy._get_materialize_cache()
                strategy._remember_materialized(
                    bucket, "f", {"n": n}, _ref("f", 2_000),
                )
            finally:
                reset_current_run_id(token)
            strategy.drop_run_cache(f"run-{n}")
        snap = strategy.budget_snapshot()
        assert snap["materialize_bytes_estimate"] == 0, (
            f"{snap['materialize_bytes_estimate']} bytes still charged after "
            "every run was dropped"
        )
        assert snap["materialize_buckets"] == 0

    def test_the_cap_is_global_not_per_run(self, strategy, monkeypatch):
        """Concurrent runs each got their own unbounded bucket before this."""
        monkeypatch.setattr(fbr, "_MATERIALIZE_CACHE_MAX_BYTES", 1_000)
        monkeypatch.setattr(fbr, "_MATERIALIZE_PARSE_AMPLIFICATION", 1)
        for n in range(20):
            token = set_current_run_id(f"run-{n}")
            try:
                bucket = strategy._get_materialize_cache()
                strategy._remember_materialized(
                    bucket, f"f-{n}", n, _ref(f"f-{n}", 200),
                )
            finally:
                reset_current_run_id(token)
        snap = strategy.budget_snapshot()
        assert snap["materialize_bytes_estimate"] <= 1_000, (
            "20 concurrent runs each kept their own bucket — the cap is "
            "per-run, so N runs means N times the ceiling"
        )


class TestSizeEstimation:
    def test_amplification_is_applied(self):
        est = FileBackedResolverStrategy._retained_bytes_estimate(
            {"size_bytes": 1_000},
        )
        assert est == 1_000 * fbr._MATERIALIZE_PARSE_AMPLIFICATION

    @pytest.mark.parametrize("bad", [None, "", "abc", -5, 0])
    def test_unusable_sizes_charge_the_unknown_rate(self, bad):
        est = FileBackedResolverStrategy._retained_bytes_estimate(
            {"size_bytes": bad},
        )
        assert est == fbr._MATERIALIZE_UNKNOWN_CHARGE

    def test_estimation_does_not_serialize_the_value(self):
        """Measuring must not allocate a second copy of what it measures.

        ``inline_output_size_bytes`` json-dumps the whole structure; using it
        here would double the peak this cap exists to lower. The estimator
        never sees the materialized value at all — only the ref.
        """
        import ast
        import inspect

        # Assert on CALLS, not mentions: the docstring names both of these to
        # explain why they are not used, and a substring match would fail on
        # its own explanation.
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(
            FileBackedResolverStrategy._retained_bytes_estimate)))
        called = {
            node.func.id if isinstance(node.func, ast.Name)
            else getattr(node.func, "attr", "")
            for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        assert "dumps" not in called, "the estimator serializes the value"
        assert "inline_output_size_bytes" not in called
        assert called <= {"int", "get"}, f"unexpected calls in the estimator: {called}"
