"""Tests for GrpcWorkerRegistryClient."""
import pytest
from unittest.mock import MagicMock, patch

grpc = pytest.importorskip("grpc", reason="grpcio not installed")
pytest.importorskip("workflow_proto.v1.worker_registry_pb2", reason="workflow_proto not installed")

from workflow_proto.v1 import worker_registry_pb2 as pb

from app.layer4_frameworks.clients.grpc_worker_registry_client import (
    GrpcWorkerRegistryClient,
    _worker_detail_to_dict,
)


def test_list_workers_returns_empty_on_error():
    client = GrpcWorkerRegistryClient("localhost:50052")
    # No server running — should gracefully return []
    with patch.object(client, "_stub") as mock_stub:
        mock_stub.return_value.ListWorkers.side_effect = Exception("connection refused")
        result = client.list_workers()
    assert result == []


def test_get_worker_returns_none_on_error():
    client = GrpcWorkerRegistryClient("localhost:50052")
    with patch.object(client, "_stub") as mock_stub:
        mock_stub.return_value.GetWorker.side_effect = Exception("connection refused")
        result = client.get_worker("w-1")
    assert result is None


def test_discover_workers_returns_empty_on_error():
    client = GrpcWorkerRegistryClient("localhost:50052")
    with patch.object(client, "_stub") as mock_stub:
        mock_stub.return_value.DiscoverWorkers.side_effect = Exception("connection refused")
        result = client.discover_workers("http-request")
    assert result == []


def test_get_worker_schema_returns_none_on_error():
    client = GrpcWorkerRegistryClient("localhost:50052")
    with patch.object(client, "_stub") as mock_stub:
        mock_stub.return_value.GetWorkerSchema.side_effect = Exception("not found")
        result = client.get_worker_schema("w-1")
    assert result is None


def test_worker_detail_mapping_carries_kind():
    """SA-1734: WorkerDetail.kind must survive the proto->dict mapping."""
    detail = pb.WorkerDetail(worker_id="w-1", worker_type="http-request", kind="read")
    result = _worker_detail_to_dict(detail)
    assert result.get("kind") == "read"


def test_worker_detail_mapping_defaults_kind_to_action():
    """SA-1734: absent kind must default to 'action' (absent => action)."""
    detail = pb.WorkerDetail(worker_id="w-2", worker_type="http-request")
    result = _worker_detail_to_dict(detail)
    assert result.get("kind") == "action"


def test_worker_detail_mapping_blank_kind_category_is_plain_string():
    """Code-review follow-up: the mapped dict's `kind` must be usable directly
    in a `category` f-string downstream (worker_node_provider) without
    rendering the NodeKind enum's repr. Assert the actual type, not just
    equality (NodeKind is a str subclass, so `== "action"` alone would not
    have caught a bare-enum regression)."""
    detail = pb.WorkerDetail(worker_id="w-2b", worker_type="http-request")
    result = _worker_detail_to_dict(detail)
    assert type(result["kind"]) is str
    assert f"Jira.{result['kind']}" == "Jira.action"


def test_worker_detail_mapping_coerces_unknown_kind_and_warns(caplog):
    """This client is a SECOND reader of the wire the executor already
    validates at registration time. A worker that predates that guard (or
    bypasses it) can still send an invented kind. It must be coerced to
    `action` -- never raise, since one bad worker must not 500 the whole
    catalog -- and it must be logged loud enough to be noticed."""
    import logging

    detail = pb.WorkerDetail(worker_id="w-3", worker_type="jira_get_issue", kind="banana")
    with caplog.at_level(logging.WARNING):
        result = _worker_detail_to_dict(detail)
    assert result.get("kind") == "action"
    assert type(result["kind"]) is str
    assert any("banana" in r.message for r in caplog.records)
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_unknown_kind_never_reaches_the_frontend_as_jira_banana():
    """End-to-end proof for the seam this fix targets: a worker registering
    with kind="banana" must never produce category="Jira.banana" for the FE."""
    from app.layer2_application.providers.worker_node_provider import _build_summary

    detail = pb.WorkerDetail(worker_id="w-4", worker_type="jira_get_issue", kind="banana")
    worker_dict = _worker_detail_to_dict(detail)
    summary = _build_summary(worker_dict)
    assert summary.category == "Jira.action"
    assert "banana" not in summary.category
