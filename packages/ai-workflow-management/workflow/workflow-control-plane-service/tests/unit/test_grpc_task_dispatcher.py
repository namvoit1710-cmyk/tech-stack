"""Tests for GrpcTaskDispatcher."""
import pytest
from unittest.mock import MagicMock, patch, ANY
from dataclasses import dataclass

grpc = pytest.importorskip("grpc", reason="grpcio not installed")
pytest.importorskip("workflow_proto.v1.task_dispatch_pb2", reason="workflow_proto not installed")

from app.layer4_frameworks.messaging.grpc_task_dispatcher import GrpcTaskDispatcher, _DISPATCH_TOPICS


@dataclass
class FakeTaskDispatched:
    task_id: str = "t-1"
    run_id: str = "r-1"
    node_id: str = "n-1"
    worker_type: str = "http-request"
    input: dict = None
    node_config: dict = None
    idempotency_key: str = "key-1"

    def __post_init__(self):
        if self.input is None:
            self.input = {"url": "https://example.com"}
        if self.node_config is None:
            self.node_config = {}


def test_delegates_to_fallback():
    fallback = MagicMock()
    dispatcher = GrpcTaskDispatcher(fallback, "localhost:50052")
    event = FakeTaskDispatched()
    dispatcher.publish("some.other.topic", event)
    fallback.publish.assert_called_once_with("some.other.topic", event)


def test_recognizes_dispatch_topics():
    for topic in _DISPATCH_TOPICS:
        fallback = MagicMock()
        dispatcher = GrpcTaskDispatcher(fallback, "localhost:50052")
        dispatcher._executor = MagicMock()
        dispatcher.publish(topic, FakeTaskDispatched())
        dispatcher._executor.submit.assert_called_once()


def _flat(**overrides) -> dict:
    base = {
        "task_id": "t-1", "run_id": "r-1", "node_id": "n-1",
        "worker_type": "worker:http_request", "input": {},
        "node_config": {}, "idempotency_key": "key-1",
    }
    base.update(overrides)
    return base


def test_flat_dict_tracking_only_is_not_dispatched():
    """Flat serialised dict with ``dispatch_to_worker=False`` → NOT dispatched.

    Regression: ``EnrichingEventPublisher`` forwards a flat ``asdict`` dict, so
    the flag is a top-level key. The old gate read it nested under ``payload``
    and let the empty task-creator dispatch through → ``'url' is required``.
    """
    dispatcher = GrpcTaskDispatcher(MagicMock(), "localhost:50052")
    dispatcher._executor = MagicMock()
    dispatcher.publish("node.dispatched", _flat(dispatch_to_worker=False))
    dispatcher._executor.submit.assert_not_called()


def test_flat_dict_authoritative_is_dispatched():
    dispatcher = GrpcTaskDispatcher(MagicMock(), "localhost:50052")
    dispatcher._executor = MagicMock()
    dispatcher.publish("node.dispatched", _flat(
        dispatch_to_worker=True, input={"url": "https://x"}))
    dispatcher._executor.submit.assert_called_once()


def test_ignores_non_dispatch_topics():
    fallback = MagicMock()
    dispatcher = GrpcTaskDispatcher(fallback, "localhost:50052")
    dispatcher._executor = MagicMock()
    dispatcher.publish("run.completed", FakeTaskDispatched())
    dispatcher._executor.submit.assert_not_called()


def test_extracts_payload_from_dataclass():
    payload = GrpcTaskDispatcher._to_payload(FakeTaskDispatched())
    assert payload["task_id"] == "t-1"
    assert payload["run_id"] == "r-1"
    assert payload["worker_type"] == "http-request"
    assert payload["input"] == {"url": "https://example.com"}


def test_extracts_payload_from_dict():
    # Flattened-dict shape produced by EnrichingEventPublisher after
    # serializing a TaskDispatched dataclass via dataclasses.asdict().
    event = {
        "task_id": "t-2",
        "run_id": "r-2",
        "node_id": "n-2",
        "worker_type": "http-request",
        "input": {"url": "https://example.com"},
        "node_config": {"timeout": 30},
        "idempotency_key": "ik-2",
    }
    payload = GrpcTaskDispatcher._to_payload(event)
    assert payload["task_id"] == "t-2"
    assert payload["worker_type"] == "http-request"
    assert payload["input"] == {"url": "https://example.com"}
    assert payload["node_config"] == {"timeout": 30}


def test_carries_spec_version_sa1490():
    # SA-1490 Phase 2: the pinned worker spec_version rides the gRPC payload.
    assert GrpcTaskDispatcher._to_payload(
        {"task_id": "t", "worker_type": "w", "spec_version": "2.1.0"}
    )["spec_version"] == "2.1.0"
    # default '' when unpinned
    assert GrpcTaskDispatcher._to_payload(
        {"task_id": "t", "worker_type": "w"}
    )["spec_version"] == ""
    assert payload["idempotency_key"] == "ik-2"


def test_returns_none_for_unsupported_event():
    assert GrpcTaskDispatcher._to_payload("not a valid event") is None


def test_no_fallback():
    dispatcher = GrpcTaskDispatcher(None, "localhost:50052")
    # Should not raise
    dispatcher.publish("run.completed", FakeTaskDispatched())


def test_builtin_worker_type_is_not_dispatched():
    """``builtin:*`` nodes are engine-inline (BUILTIN) or pause-and-wait (PENDING).

    Forwarding their ``node.dispatched`` events to the executor caused a
    HUMAN_ACTION auto-complete bug (the executor's builtin handler called
    back with output_port="success" before the user submitted, racing the
    run to ``completed`` and stranding the PATCH/submit with 412/409).
    """
    for kind in ("human_action", "input", "data_edit", "file_upload",
                 "condition", "switch", "loop", "compute"):
        fallback = MagicMock()
        dispatcher = GrpcTaskDispatcher(fallback, "localhost:50052")
        dispatcher._executor = MagicMock()
        dispatcher.publish(
            "node.dispatched",
            FakeTaskDispatched(worker_type=f"builtin:{kind}"),
        )
        dispatcher._executor.submit.assert_not_called()
        # Fallback publisher is always still notified.
        fallback.publish.assert_called_once()
