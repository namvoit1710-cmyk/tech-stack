"""Phase M.3.5 — end-to-end cross-instance EditNodeDataAction.

Simulates two control-plane instances sharing a broker. Instance A
(HTTP caller) enqueues an EditNodeDataAction; Kafka partition-by-run_id
delivers it to Instance B's worker (which owns that run's partition);
Instance B publishes the response back onto Instance A's response
topic; Instance A's listener resolves the local future.

The broker stub is deliberately minimal — just topic → callbacks — so
the test exercises the wiring rather than re-testing Kafka itself.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
    EditNodeDataCommand,
    EditNodeDataResult,
)
from app.layer2_application.interfaces.orchestration_queue_interface import (
    EditNodeDataAction,
)
from app.layer4_frameworks.orchestration_queue.kafka_queue import (
    BrokerOrchestrationQueue,
    ORCHESTRATION_TOPIC,
)
from app.layer4_frameworks.orchestration_queue.response_registry import ResponseRegistry
from app.layer4_frameworks.orchestration_queue.response_topic_listener import (
    ResponseTopicListener,
    response_topic_for,
)
from app.layer4_frameworks.orchestration_queue.worker import OrchestrationWorker


class _InMemoryBroker:
    """Topic → callbacks bridge. No partition/consumer-group semantics;
    callers control routing by who subscribes to what.
    """

    def __init__(self) -> None:
        self._subs: dict[str, list] = {}

    def publish_to_topic(self, topic: str, message, key: str | None = None) -> None:
        for cb in self._subs.get(topic, []):
            cb(message)

    def subscribe_to_topic(self, topic: str, callback) -> None:
        self._subs.setdefault(topic, []).append(callback)

    def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_edit_node_data_crosses_instances_and_resolves_origin_future():
    """The enqueueing instance (A) awaits a local future; the processing
    instance (B) publishes the response to A's response topic; A's
    listener resolves the future. Same pattern production Kafka takes.
    """
    broker = _InMemoryBroker()

    # ── Instance A: origin. Has queue (as publisher) + registry + listener. ──
    registry_a = ResponseRegistry()
    queue_a = BrokerOrchestrationQueue(broker)
    # A does NOT start_consumer — we simulate Kafka's partition
    # ownership by only having B subscribe to orchestration.actions.
    listener_a = ResponseTopicListener(broker, "inst-A", registry_a)
    listener_a.start()

    # ── Instance B: processor. Has worker wired against the same broker. ──
    registry_b = ResponseRegistry()  # unused; B only publishes responses
    expected_result = EditNodeDataResult(etag="e-cross", version=7)
    use_case_b = MagicMock()
    use_case_b.execute = AsyncMock(return_value=expected_result)

    worker_b = OrchestrationWorker(
        queue=MagicMock(),  # not used; we dispatch directly in this harness
        engine=MagicMock(),
        edit_node_data_use_case=use_case_b,
        response_registry=registry_b,
        instance_id="inst-B",
        broker_client=broker,
    )

    # Subscribe B to orchestration.actions the same way BrokerOrchestrationQueue
    # would on a real pod. When A publishes, B's callback fires directly.
    delivered: list[EditNodeDataAction] = []

    def _actions_callback(msg):
        from app.layer4_frameworks.orchestration_queue.kafka_queue import _deserialize
        action = _deserialize(msg)
        assert action is not None
        delivered.append(action)

    broker.subscribe_to_topic(ORCHESTRATION_TOPIC, _actions_callback)

    # ── Instance A's controller flow: allocate future, enqueue action. ──
    correlation_id, future = registry_a.allocate()
    try:
        action = EditNodeDataAction(
            run_id="r-cross",
            correlation_id=correlation_id,
            command=EditNodeDataCommand(run_id="r-cross", node_id="n-1"),
            origin_instance_id="inst-A",
        )
        await queue_a.enqueue(action)

        # ── Instance B receives + processes the action. ──
        assert len(delivered) == 1
        assert delivered[0].origin_instance_id == "inst-A"
        assert isinstance(delivered[0].command, EditNodeDataCommand)
        await worker_b._dispatch_edit_node_data(delivered[0])

        # ── Instance A's future should now be resolved via the response topic. ──
        result = await asyncio.wait_for(future, timeout=1.0)
        assert isinstance(result, EditNodeDataResult)
        assert result.etag == "e-cross"
        assert result.version == 7
        # Instance A's use-case mock was never invoked — the call
        # happened on B. This is the whole point of the flow.
        use_case_b.execute.assert_awaited_once()
    finally:
        registry_a.release(correlation_id)


@pytest.mark.asyncio
async def test_cross_instance_exception_propagates_to_origin_future():
    """When B's use case raises, A's future is rejected with a
    CrossInstanceExecutionError carrying the remote exception class +
    message. Verifies error payload round-trip.
    """
    broker = _InMemoryBroker()

    registry_a = ResponseRegistry()
    listener_a = ResponseTopicListener(broker, "inst-A", registry_a)
    listener_a.start()

    use_case_b = MagicMock()
    use_case_b.execute = AsyncMock(side_effect=RuntimeError("remote-boom"))
    worker_b = OrchestrationWorker(
        queue=MagicMock(),
        engine=MagicMock(),
        edit_node_data_use_case=use_case_b,
        response_registry=ResponseRegistry(),
        instance_id="inst-B",
        broker_client=broker,
    )

    correlation_id, future = registry_a.allocate()
    try:
        action = EditNodeDataAction(
            run_id="r-err",
            correlation_id=correlation_id,
            command=EditNodeDataCommand(run_id="r-err", node_id="n-1"),
            origin_instance_id="inst-A",
        )
        with pytest.raises(RuntimeError):
            # The worker re-raises for its loop's error-logging path;
            # the publish to A's response topic happens BEFORE the raise.
            await worker_b._dispatch_edit_node_data(action)

        from app.layer4_frameworks.orchestration_queue.response_topic_listener import (
            CrossInstanceExecutionError,
        )
        with pytest.raises(CrossInstanceExecutionError) as excinfo:
            await asyncio.wait_for(future, timeout=1.0)
        assert excinfo.value.exception_class == "RuntimeError"
        assert "remote-boom" in str(excinfo.value)
    finally:
        registry_a.release(correlation_id)


@pytest.mark.asyncio
async def test_response_topic_isolates_instances():
    """A response published to A's topic must NOT be delivered to B's
    listener. Prevents cross-instance future confusion when many
    instances share a cluster.
    """
    broker = _InMemoryBroker()

    registry_a = ResponseRegistry()
    registry_b = ResponseRegistry()
    ResponseTopicListener(broker, "inst-A", registry_a).start()
    ResponseTopicListener(broker, "inst-B", registry_b).start()

    # Publish a response that targets A's topic only.
    broker.publish_to_topic(
        response_topic_for("inst-A"),
        {
            "correlation_id": "corr-isolate",
            "result": {
                "etag": "e", "version": 1, "file_lineage": {},
                "results": [], "rerun": {
                    "strategy": "none", "triggered": False,
                    "degraded_from": None, "reset_node_ids": [], "task_ids": [],
                },
                "error": None, "error_code": "", "error_details": {},
            },
            "error": None,
        },
    )
    # Neither registry had a future for corr-isolate — the resolve is a
    # no-op for both. The test guards against accidental broadcast: if
    # the listener subscribed to the wrong topic, corr-isolate would
    # still be a no-op, so we verify routing by inspecting topic names
    # on the broker's subscription map.
    assert response_topic_for("inst-A") in broker._subs
    assert response_topic_for("inst-B") in broker._subs
    assert len(broker._subs[response_topic_for("inst-A")]) == 1
    assert len(broker._subs[response_topic_for("inst-B")]) == 1
