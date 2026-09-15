"""Tests for HttpTaskDispatcher."""
from dataclasses import dataclass
from unittest.mock import MagicMock

from app.layer4_frameworks.messaging.http_task_dispatcher import (
    HttpTaskDispatcher,
    _DISPATCH_TOPICS,
)


@dataclass
class FakeTaskDispatched:
    task_id: str = "t-1"
    run_id: str = "r-1"
    node_id: str = "n-1"
    worker_type: str = "http-request"
    input: dict | None = None
    node_config: dict | None = None
    idempotency_key: str = "key-1"

    def __post_init__(self) -> None:
        if self.input is None:
            self.input = {"url": "https://example.com"}
        if self.node_config is None:
            self.node_config = {}


def _dispatcher() -> HttpTaskDispatcher:
    d = HttpTaskDispatcher(MagicMock(), "http://localhost:8004")
    d._executor = MagicMock()
    return d


def test_real_worker_task_is_dispatched():
    d = _dispatcher()
    for topic in _DISPATCH_TOPICS:
        d._executor.reset_mock()
        d.publish(topic, FakeTaskDispatched(worker_type="http-request"))
        d._executor.submit.assert_called_once()


def test_builtin_pending_is_not_dispatched():
    """HUMAN_ACTION must not be POSTed to the executor.

    The engine emits ``node.dispatched`` for PENDING nodes as a tracking
    signal. Forwarding to the executor causes a phantom completion
    (output_port="success") that races the user's submit and runs the
    whole workflow to ``completed`` before the human can respond.
    """
    d = _dispatcher()
    for kind in ("human_action", "input", "data_edit", "file_upload"):
        d._executor.reset_mock()
        d.publish("node.dispatched", FakeTaskDispatched(worker_type=f"builtin:{kind}"))
        d._executor.submit.assert_not_called()


def test_builtin_inline_is_not_dispatched():
    """BUILTIN nodes (CONDITION, SWITCH, …) are engine-inline, not worker tasks."""
    d = _dispatcher()
    for kind in ("condition", "switch", "loop", "parallel", "merge", "compute"):
        d._executor.reset_mock()
        d.publish("node.dispatched", FakeTaskDispatched(worker_type=f"builtin:{kind}"))
        d._executor.submit.assert_not_called()


def test_fallback_is_always_invoked():
    """The decorator must still hand the event to the inner publisher."""
    fallback = MagicMock()
    d = HttpTaskDispatcher(fallback, "http://localhost:8004")
    d._executor = MagicMock()
    event = FakeTaskDispatched(worker_type="builtin:human_action")
    d.publish("node.dispatched", event)
    fallback.publish.assert_called_once_with("node.dispatched", event)


def test_empty_worker_type_is_skipped():
    d = _dispatcher()
    d.publish("node.dispatched", FakeTaskDispatched(worker_type=""))
    d._executor.submit.assert_not_called()


@dataclass
class FakeTrackingDispatched(FakeTaskDispatched):
    """A lifecycle/tracking-only NodeDispatched (e.g. TaskCreatorConsumer's)."""
    dispatch_to_worker: bool = False


def test_tracking_only_dispatch_is_not_forwarded_to_worker():
    """``dispatch_to_worker=False`` events must NOT be POSTed to the worker.

    Regression: under EXECUTOR_MODE=shadow/executor, TaskCreatorConsumer
    emits a tracking-only NodeDispatched carrying empty input. Forwarding it
    dispatched the TASK with no ``url`` → worker failed ``'url' is required``.
    """
    d = _dispatcher()
    for topic in _DISPATCH_TOPICS:
        d._executor.reset_mock()
        d.publish(topic, FakeTrackingDispatched(worker_type="http-request"))
        d._executor.submit.assert_not_called()


def test_tracking_only_dispatch_still_reaches_fallback():
    """The tracking event must still reach the inner publisher (projector/FE)."""
    fallback = MagicMock()
    d = HttpTaskDispatcher(fallback, "http://localhost:8004")
    d._executor = MagicMock()
    event = FakeTrackingDispatched(worker_type="http-request")
    d.publish("node.dispatched", event)
    fallback.publish.assert_called_once_with("node.dispatched", event)
    d._executor.submit.assert_not_called()


def test_default_event_without_flag_is_dispatched():
    """Events with no ``dispatch_to_worker`` field default to True (forwarded)."""
    d = _dispatcher()
    d.publish("node.dispatched", FakeTaskDispatched(worker_type="http-request"))
    d._executor.submit.assert_called_once()


# ---------------------------------------------------------------------------
# FLAT-DICT path — the shape that actually reaches this wrapper in production.
# ``EnrichingEventPublisher`` serialises the NodeDispatched dataclass via
# ``dataclasses.asdict`` and forwards the flat dict inward, so the gate sees a
# dict with ``dispatch_to_worker`` as a TOP-LEVEL key. The earlier gate only
# read ``event["payload"]["dispatch_to_worker"]`` → missed the flag → the
# tracking event reached the worker (``'url' is required``). The dataclass
# tests above never exercised this path, which is why the suite stayed green.
# ---------------------------------------------------------------------------

def _flat_dispatched(**overrides) -> dict:
    base = {
        "task_id": "t-1", "run_id": "r-1", "node_id": "n-1",
        "worker_type": "worker:http_request", "input": {},
        "node_config": {}, "idempotency_key": "key-1",
    }
    base.update(overrides)
    return base


def test_flat_dict_tracking_only_is_not_dispatched():
    """Flat serialised dict with ``dispatch_to_worker=False`` → NOT dispatched."""
    d = _dispatcher()
    for topic in _DISPATCH_TOPICS:
        d._executor.reset_mock()
        d.publish(topic, _flat_dispatched(dispatch_to_worker=False))
        d._executor.submit.assert_not_called()


def test_flat_dict_authoritative_is_dispatched():
    """Flat dict with ``dispatch_to_worker=True`` → dispatched (the real one)."""
    d = _dispatcher()
    d.publish("node.dispatched", _flat_dispatched(
        dispatch_to_worker=True, input={"url": "https://x"}))
    d._executor.submit.assert_called_once()


def test_flat_dict_without_flag_defaults_to_dispatched():
    """Flat dict with no flag → safe default True (forwarded)."""
    d = _dispatcher()
    d.publish("node.dispatched", _flat_dispatched())
    d._executor.submit.assert_called_once()


def test_nested_payload_envelope_flag_still_honoured():
    """Defensive: a ``payload``-wrapped envelope still suppresses the worker."""
    d = _dispatcher()
    d.publish("node.dispatched", {"payload": {"dispatch_to_worker": False},
                                  "worker_type": "worker:http_request"})
    d._executor.submit.assert_not_called()


def test_dispatch_payload_includes_run_metadata():
    """Caller-supplied run metadata is forwarded to the worker HTTP payload."""

    @dataclass
    class _Dispatched:
        task_id: str = "t-1"
        run_id: str = "r-1"
        node_id: str = "n-1"
        worker_type: str = "http-request"
        input: dict | None = None
        node_config: dict | None = None
        idempotency_key: str = "key-1"
        metadata: dict | None = None

        def __post_init__(self) -> None:
            self.input = self.input or {}
            self.node_config = self.node_config or {}
            self.metadata = self.metadata or {"source_system": "crm"}

    payload = HttpTaskDispatcher._to_dispatch_payload(_Dispatched())
    assert payload["metadata"] == {"source_system": "crm"}


def test_dispatch_payload_metadata_defaults_to_empty():
    """Events without a metadata field still produce a valid payload."""
    payload = HttpTaskDispatcher._to_dispatch_payload(FakeTaskDispatched())
    assert payload["metadata"] == {}
