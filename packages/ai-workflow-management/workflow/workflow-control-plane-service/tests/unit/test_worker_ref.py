"""Tests for the WorkerRef value object (the 'worker:' prefix convention)."""

from app.layer1_domain.value_objects.worker_ref import WorkerRef


class TestWorkerRefParse:
    def test_strips_prefix(self):
        ref = WorkerRef.parse("worker:http_request")
        assert ref is not None
        assert ref.worker_type == "http_request"

    def test_bare_type_is_noop(self):
        ref = WorkerRef.parse("http_request")
        assert ref is not None
        assert ref.worker_type == "http_request"

    def test_none_and_empty(self):
        assert WorkerRef.parse(None) is None
        assert WorkerRef.parse("") is None


class TestWorkerRefFromCatalogId:
    def test_worker_id(self):
        ref = WorkerRef.from_catalog_id("worker:http_request")
        assert ref is not None
        assert ref.worker_type == "http_request"

    def test_non_worker_ids_rejected(self):
        assert WorkerRef.from_catalog_id("builtin:condition") is None
        assert WorkerRef.from_catalog_id("business:abc") is None
        assert WorkerRef.from_catalog_id("http_request") is None  # bare, no prefix
        assert WorkerRef.from_catalog_id("worker:") is None  # empty type
        assert WorkerRef.from_catalog_id("") is None


class TestWorkerRefCatalogId:
    def test_round_trip(self):
        assert WorkerRef(worker_type="http_request").catalog_id == "worker:http_request"
        assert WorkerRef.parse("worker:http_request").catalog_id == "worker:http_request"
