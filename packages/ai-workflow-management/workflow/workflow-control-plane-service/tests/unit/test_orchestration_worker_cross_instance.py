"""Phase M.3.3 — OrchestrationWorker cross-instance response routing.

The worker takes one of two paths after processing an EditNodeDataAction:
- Local fast path: origin_instance_id is empty OR matches self → resolve
  the local ResponseRegistry directly (no broker publish).
- Cross-instance: origin_instance_id differs and a broker is wired →
  publish the response to ``orchestration.responses.{origin_instance_id}``
  where the origin's ResponseTopicListener will pick it up.
"""
from __future__ import annotations

import asyncio
import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
    EditNodeDataCommand,
    EditNodeDataResult,
)
from app.layer2_application.interfaces.orchestration_queue_interface import (
    EditNodeDataAction,
)
from app.layer4_frameworks.orchestration_queue.response_registry import ResponseRegistry
from app.layer4_frameworks.orchestration_queue.worker import OrchestrationWorker


def _make_action(*, origin: str, correlation_id: str = "corr-1") -> EditNodeDataAction:
    return EditNodeDataAction(
        run_id="r-1",
        correlation_id=correlation_id,
        command=EditNodeDataCommand(run_id="r-1", node_id="n-1"),
        origin_instance_id=origin,
    )


def _make_result() -> EditNodeDataResult:
    return EditNodeDataResult(etag="e-1", version=2)


def _make_worker(*, instance_id: str, broker, use_case, registry) -> OrchestrationWorker:
    return OrchestrationWorker(
        queue=MagicMock(),
        engine=MagicMock(),
        edit_node_data_use_case=use_case,
        response_registry=registry,
        instance_id=instance_id,
        broker_client=broker,
    )


@pytest.mark.asyncio
class TestLocalFastPath:
    async def test_empty_origin_resolves_local_registry(self):
        registry = ResponseRegistry()
        correlation_id, future = registry.allocate()

        use_case = MagicMock()
        use_case.execute = AsyncMock(return_value=_make_result())
        broker = MagicMock()
        worker = _make_worker(
            instance_id="inst-local", broker=broker,
            use_case=use_case, registry=registry,
        )

        action = _make_action(origin="", correlation_id=correlation_id)
        await worker._dispatch_edit_node_data(action)

        # Broker NOT used — local resolve fast path.
        broker.publish_to_topic.assert_not_called()
        result = await asyncio.wait_for(future, timeout=1.0)
        assert result.etag == "e-1"

        registry.release(correlation_id)

    async def test_matching_origin_resolves_local_registry(self):
        registry = ResponseRegistry()
        correlation_id, future = registry.allocate()

        use_case = MagicMock()
        use_case.execute = AsyncMock(return_value=_make_result())
        broker = MagicMock()
        worker = _make_worker(
            instance_id="inst-A", broker=broker,
            use_case=use_case, registry=registry,
        )

        action = _make_action(origin="inst-A", correlation_id=correlation_id)
        await worker._dispatch_edit_node_data(action)

        broker.publish_to_topic.assert_not_called()
        result = await asyncio.wait_for(future, timeout=1.0)
        assert result.version == 2

        registry.release(correlation_id)

    async def test_no_broker_falls_back_to_local_even_if_origin_differs(self):
        # If the worker was built without a broker client (memory-only
        # deployment), there's no way to publish cross-instance — the
        # only sane option is to resolve locally. Configuration bug, but
        # crash-less.
        registry = ResponseRegistry()
        correlation_id, future = registry.allocate()

        use_case = MagicMock()
        use_case.execute = AsyncMock(return_value=_make_result())
        worker = _make_worker(
            instance_id="inst-A", broker=None,
            use_case=use_case, registry=registry,
        )

        action = _make_action(origin="inst-B", correlation_id=correlation_id)
        await worker._dispatch_edit_node_data(action)

        result = await asyncio.wait_for(future, timeout=1.0)
        assert result.etag == "e-1"

        registry.release(correlation_id)


@pytest.mark.asyncio
class TestCrossInstancePublish:
    async def test_foreign_origin_publishes_to_origin_topic(self):
        # Worker on instance B processes an action enqueued by instance A.
        # Must publish response onto ``orchestration.responses.<inst-A shard>``,
        # never touch its own local registry. The instance shard is the
        # dash-stripped, truncated form — see ``response_topic_for`` for
        # the queue-name length budget.
        registry = ResponseRegistry()
        use_case = MagicMock()
        use_case.execute = AsyncMock(return_value=_make_result())
        broker = MagicMock()

        worker = _make_worker(
            instance_id="inst-B", broker=broker,
            use_case=use_case, registry=registry,
        )
        action = _make_action(origin="inst-A")

        await worker._dispatch_edit_node_data(action)

        broker.publish_to_topic.assert_called_once()
        call = broker.publish_to_topic.call_args
        topic = call.args[0] if call.args else call.kwargs["topic"]
        payload = call.args[1] if len(call.args) > 1 else call.kwargs["message"]
        # SA-2087: shard = sha256("inst-A")[:8] = 70e87750 (was the compacted "instA").
        assert topic == "orchestration.responses.70e87750"
        assert payload["correlation_id"] == "corr-1"
        assert payload["error"] is None
        assert payload["result"] == dataclasses.asdict(_make_result())
        assert call.kwargs.get("key") == "corr-1"

    async def test_foreign_origin_exception_publishes_error_payload(self):
        registry = ResponseRegistry()
        use_case = MagicMock()
        use_case.execute = AsyncMock(side_effect=ValueError("nope"))
        broker = MagicMock()

        worker = _make_worker(
            instance_id="inst-B", broker=broker,
            use_case=use_case, registry=registry,
        )
        action = _make_action(origin="inst-A")

        # Dispatch re-raises to keep the worker loop's error logging path;
        # the test just verifies the publish happened BEFORE the re-raise.
        with pytest.raises(ValueError):
            await worker._dispatch_edit_node_data(action)

        broker.publish_to_topic.assert_called_once()
        payload = broker.publish_to_topic.call_args.args[1]
        assert payload["correlation_id"] == "corr-1"
        assert payload["result"] is None
        assert payload["error"]["class"] == "ValueError"
        assert payload["error"]["message"] == "nope"


@pytest.mark.asyncio
class TestBackCompatWithNoResponseRegistry:
    async def test_worker_without_registry_still_publishes_cross_instance(self):
        # A worker built without a registry (cross-instance node in a
        # multi-instance deployment) MUST still publish the response.
        # The origin instance's registry has the future.
        use_case = MagicMock()
        use_case.execute = AsyncMock(return_value=_make_result())
        broker = MagicMock()

        worker = _make_worker(
            instance_id="inst-B", broker=broker,
            use_case=use_case, registry=None,
        )
        action = _make_action(origin="inst-A")

        await worker._dispatch_edit_node_data(action)
        broker.publish_to_topic.assert_called_once()
