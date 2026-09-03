"""
Tests for runtime-scoped workflow event emitter and helper API.
"""

import asyncio
from typing import get_args, get_type_hints
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer2_application.interfaces.push_gateway_notifier import (
    PushGatewayNotifierProtocol,
)

# ─────────────────────────────────────────────────────────────────────────────
# IWorkflowEventEmitter protocol
# ─────────────────────────────────────────────────────────────────────────────


def test_iworkflow_event_emitter_importable():
    from agent_sdk.layer2_application.interfaces.workflow_event_emitter import (
        IWorkflowEventEmitter,
    )

    assert IWorkflowEventEmitter is not None


def test_iworkflow_event_emitter_has_emit_method():
    from agent_sdk.layer2_application.interfaces.workflow_event_emitter import (
        IWorkflowEventEmitter,
    )

    assert hasattr(IWorkflowEventEmitter, "emit")


def test_iworkflow_event_emitter_is_protocol():
    from typing import Protocol

    from agent_sdk.layer2_application.interfaces.workflow_event_emitter import (
        IWorkflowEventEmitter,
    )

    assert hasattr(IWorkflowEventEmitter, "__protocol_attrs__") or issubclass(
        IWorkflowEventEmitter, Protocol
    )


# ─────────────────────────────────────────────────────────────────────────────
# WorkflowEventEmitter service
# ─────────────────────────────────────────────────────────────────────────────


def test_workflow_event_emitter_importable():
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    assert WorkflowEventEmitter is not None


def test_workflow_event_emitter_accepts_publisher():
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)
    assert emitter is not None


def test_workflow_event_emitter_accepts_optional_logger():
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    mock_logger = MagicMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher, logger=mock_logger)
    assert emitter is not None


@pytest.mark.asyncio
async def test_emit_publishes_to_event_type_topic_when_no_reply_to():
    """Without a reply_to in scope, topic should be event.event_type."""
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    event = NodeUpdatedEvent(
        conv_id="conv-1", correlation_id="corr-1", workflow_id="wf-1"
    )
    await emitter.emit(event)

    mock_publisher.publish.assert_called_once()
    call_args = mock_publisher.publish.call_args
    assert call_args[0][0] == "NODE_UPDATED"


@pytest.mark.asyncio
async def test_emit_publishes_to_reply_to_progress_topic_when_reply_to_present():
    """If reply_to is in the runtime scope, topic should be '{reply_to}.progress'."""
    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest
    from agent_sdk.layer1_domain.entities.workflow_event import NodeStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)
    request = AgentRequest(message="hi", reply_to="orchestrator.001.response")

    event = NodeStartedEvent(conv_id="conv-2", correlation_id="corr-2")
    with workflow_event_scope(emitter, request=request, mode="execute"):
        await emitter.emit(event)

    mock_publisher.publish.assert_called_once()
    call_args = mock_publisher.publish.call_args
    assert call_args[0][0] == "orchestrator.001.response.progress"


@pytest.mark.asyncio
async def test_emit_uses_correlation_id_as_key():
    """Publisher should be called with key=correlation_id."""
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    event = WorkflowStartedEvent(correlation_id="corr-xyz")
    await emitter.emit(event)

    call_kwargs = mock_publisher.publish.call_args[1]
    assert call_kwargs.get("key") == "corr-xyz"


@pytest.mark.asyncio
async def test_emit_payload_contains_all_event_fields():
    """Published payload dict must contain all WorkflowEvent fields."""
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowCompletedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    event = WorkflowCompletedEvent(
        conv_id="c1", correlation_id="corr-1", workflow_id="wf-1"
    )
    await emitter.emit(event)

    payload = mock_publisher.publish.call_args[0][1]
    assert "event_type" in payload
    assert "event_id" in payload
    assert "message" in payload
    assert "conv_id" in payload
    assert "correlation_id" in payload
    assert payload["event_type"] == "WORKFLOW_COMPLETED"
    assert payload["conv_id"] == "c1"


@pytest.mark.asyncio
async def test_emit_does_not_override_already_set_fields():
    """Fields explicitly set on the event must NOT be overwritten by runtime scope."""
    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)
    request = AgentRequest(message="hi", conv_id="scope-conv")

    event = NodeUpdatedEvent(
        conv_id="explicit-conv",
        correlation_id="explicit-corr",
        workflow_id="explicit-wf",
        node_id="explicit-node",
    )
    with workflow_event_scope(emitter, request=request, mode="execute"):
        await emitter.emit(event)

    payload = mock_publisher.publish.call_args[0][1]
    assert payload["conv_id"] == "explicit-conv"
    assert payload["correlation_id"] == "explicit-corr"
    assert payload["workflow_id"] == "explicit-wf"
    assert payload["node_id"] == "explicit-node"


@pytest.mark.asyncio
async def test_emit_fills_missing_conv_id_from_request_scope():
    """If conv_id is blank on event, fill from request.conv_id in scope."""
    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest
    from agent_sdk.layer1_domain.entities.workflow_event import NodeStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)
    request = AgentRequest(message="hi", conv_id="scope-conv-id")

    event = NodeStartedEvent()
    with workflow_event_scope(emitter, request=request, mode="execute"):
        await emitter.emit(event)

    payload = mock_publisher.publish.call_args[0][1]
    assert payload["conv_id"] == "scope-conv-id"


@pytest.mark.asyncio
async def test_emit_fills_missing_correlation_id_from_request_scope():
    """correlation_id from the event is preserved; if the event has a dynamic UUID
    correlation_id, it is not overridden by the request scope (scope override only
    applies to the legacy 'auto-generated' sentinel)."""
    import uuid as _uuid

    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest
    from agent_sdk.layer1_domain.entities.workflow_event import NodeStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)
    request = AgentRequest(message="hi", correlation_id="scope-corr-id")

    event = NodeStartedEvent()
    event_corr_id = event.correlation_id
    _uuid.UUID(event_corr_id)
    with workflow_event_scope(emitter, request=request, mode="execute"):
        await emitter.emit(event)

    payload = mock_publisher.publish.call_args[0][1]
    assert payload["correlation_id"] == event_corr_id


@pytest.mark.asyncio
async def test_emit_fills_timestamp_when_at_epoch_default():
    """timestamp is a unix float by default; the emitter converts it to an ISO-8601 string."""
    import time as _time

    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    _time.time()
    event = WorkflowStartedEvent()
    assert isinstance(event.timestamp, float)
    await emitter.emit(event)

    payload = mock_publisher.publish.call_args[0][1]
    ts = payload["timestamp"]
    assert isinstance(ts, str), f"Expected ISO string, got {type(ts)}: {ts}"
    assert "T" in ts


@pytest.mark.asyncio
async def test_emit_with_explicit_topic_overrides_default():
    """Passing topic= kwarg to emit() must override both default and scope topic."""
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    event = WorkflowStartedEvent()
    await emitter.emit(event, topic="my.custom.topic")

    call_args = mock_publisher.publish.call_args
    assert call_args[0][0] == "my.custom.topic"


@pytest.mark.asyncio
async def test_emit_with_node_id_kwarg_fills_missing_node_id():
    """Passing node_id= to emit() fills blank node_id on the published payload."""
    from agent_sdk.layer1_domain.entities.workflow_event import NodeStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    event = NodeStartedEvent()
    await emitter.emit(event, node_id="node-42")

    payload = mock_publisher.publish.call_args[0][1]
    assert payload["node_id"] == "node-42"


@pytest.mark.asyncio
async def test_emit_publish_failure_is_non_fatal():
    """If publisher.publish raises, emit() must not raise - log and continue."""
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    mock_publisher.publish.side_effect = Exception("broker down")
    mock_logger = MagicMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher, logger=mock_logger)

    event = WorkflowStartedEvent()
    await emitter.emit(event)

    mock_logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_emit_publish_failure_is_logged_not_silently_dropped():
    """Publish failure must call logger.error (not silently ignore)."""
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowFailedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    mock_publisher.publish.side_effect = RuntimeError("network error")
    mock_logger = MagicMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher, logger=mock_logger)

    event = WorkflowFailedEvent()
    await emitter.emit(event)

    logged_args = mock_logger.error.call_args
    assert logged_args is not None
    log_msg = str(logged_args)
    assert (
        "network error" in log_msg
        or "broker" in log_msg
        or "publish" in log_msg.lower()
    )


@pytest.mark.asyncio
async def test_emit_publish_failure_raises_when_raise_on_error_enabled():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowFailedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    mock_publisher.publish.side_effect = RuntimeError("network error")
    mock_logger = MagicMock()
    emitter = WorkflowEventEmitter(
        publisher=mock_publisher,
        logger=mock_logger,
        raise_on_error=True,
    )

    event = WorkflowFailedEvent()

    with pytest.raises(RuntimeError, match="network error"):
        await emitter.emit(event)

    mock_logger.error.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# workflow_event_runtime helpers
# ─────────────────────────────────────────────────────────────────────────────


def test_workflow_event_runtime_importable():
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        emit_workflow_event,
        workflow_event_scope,
    )

    assert callable(workflow_event_scope)
    assert callable(emit_workflow_event)


def test_workflow_event_scope_is_context_manager():
    """workflow_event_scope must work as a synchronous context manager."""
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )

    mock_emitter = MagicMock()
    with workflow_event_scope(mock_emitter) as scope:
        assert scope is not None


@pytest.mark.asyncio
async def test_emit_workflow_event_delegates_to_active_scope():
    """emit_workflow_event() must delegate to the emitter set by workflow_event_scope."""
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        emit_workflow_event,
        workflow_event_scope,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)
    event = NodeUpdatedEvent(conv_id="c1", correlation_id="corr-1")

    with workflow_event_scope(emitter):
        await emit_workflow_event(event)

    mock_publisher.publish.assert_called_once()


@pytest.mark.asyncio
async def test_emit_workflow_event_outside_scope_is_non_fatal():
    """emit_workflow_event() called with no active scope must not raise."""
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        emit_workflow_event,
    )

    event = WorkflowStartedEvent()
    await emit_workflow_event(event)


@pytest.mark.asyncio
async def test_emit_workflow_event_passes_kwargs_to_emitter():
    """Kwargs passed to emit_workflow_event() are forwarded to emitter.emit()."""
    from agent_sdk.layer1_domain.entities.workflow_event import NodeStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        emit_workflow_event,
        workflow_event_scope,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)
    event = NodeStartedEvent()

    with workflow_event_scope(emitter):
        await emit_workflow_event(event, node_id="node-7")

    payload = mock_publisher.publish.call_args[0][1]
    assert payload["node_id"] == "node-7"


@pytest.mark.asyncio
async def test_scope_isolation_between_concurrent_contexts():
    """Each scope context var must be independent (no cross-contamination)."""
    from agent_sdk.layer1_domain.entities.workflow_event import NodeStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        emit_workflow_event,
        workflow_event_scope,
    )

    publisher_a = AsyncMock()
    publisher_b = AsyncMock()
    emitter_a = WorkflowEventEmitter(publisher=publisher_a)
    emitter_b = WorkflowEventEmitter(publisher=publisher_b)

    event_a = NodeStartedEvent(conv_id="conv-a")
    event_b = NodeStartedEvent(conv_id="conv-b")

    async def run_a():
        with workflow_event_scope(emitter_a):
            await emit_workflow_event(event_a)

    async def run_b():
        with workflow_event_scope(emitter_b):
            await emit_workflow_event(event_b)

    await asyncio.gather(run_a(), run_b())

    publisher_a.publish.assert_called_once()
    publisher_b.publish.assert_called_once()
    payload_a = publisher_a.publish.call_args[0][1]
    payload_b = publisher_b.publish.call_args[0][1]
    assert payload_a["conv_id"] == "conv-a"
    assert payload_b["conv_id"] == "conv-b"


# ─────────────────────────────────────────────────────────────────────────────
# Serialization helper: event to dict preserves schema
# ─────────────────────────────────────────────────────────────────────────────


def test_event_to_dict_preserves_all_keys():
    """serialize_event() must return all WorkflowEvent field keys unchanged."""
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        serialize_event,
    )

    event = WorkflowStartedEvent(
        conv_id="c1",
        correlation_id="corr",
        workflow_id="wf",
        node_id="n1",
    )
    result = serialize_event(event)
    expected_keys = {
        "event_id",
        "event_type",
        "message",
        "conv_id",
        "correlation_id",
        "timestamp",
        "workflow_id",
        "node_id",
        "data",
    }
    assert expected_keys.issubset(set(result.keys()))


def test_event_to_dict_does_not_alter_values():
    """serialize_event() must not transform or rename payload keys."""
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        serialize_event,
    )

    event = NodeUpdatedEvent(
        conv_id="my-conv",
        correlation_id="my-corr",
        workflow_id="my-wf",
        node_id="my-node",
    )
    result = serialize_event(event)
    assert result["event_type"] == "NODE_UPDATED"
    assert result["conv_id"] == "my-conv"
    assert result["correlation_id"] == "my-corr"
    assert result["workflow_id"] == "my-wf"
    assert result["node_id"] == "my-node"
    assert "json_data" in result["data"]


def test_serialize_event_works_for_all_15_event_subclasses():
    """serialize_event() must work for every WorkflowEvent subclass."""
    from agent_sdk.layer1_domain.entities.workflow_event import (
        AgentSelectedEvent,
        InputUpdatedEvent,
        InputValidatingEvent,
        NodeCompletedEvent,
        NodeDataInitializedEvent,
        NodeStartedEvent,
        NodeUpdatedEvent,
        NodeWaitingUserEvent,
        ToolSelectedEvent,
        UiRenderRequestEvent,
        UnsupportedFeatureEvent,
        WorkflowCompletedEvent,
        WorkflowFailedEvent,
        WorkflowGuidelineRenderRequestEvent,
        WorkflowStartedEvent,
    )
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        serialize_event,
    )

    for cls in [
        WorkflowStartedEvent,
        UiRenderRequestEvent,
        WorkflowGuidelineRenderRequestEvent,
        NodeStartedEvent,
        InputValidatingEvent,
        NodeWaitingUserEvent,
        InputUpdatedEvent,
        NodeCompletedEvent,
        WorkflowCompletedEvent,
        WorkflowFailedEvent,
        UnsupportedFeatureEvent,
        NodeUpdatedEvent,
        NodeDataInitializedEvent,
        AgentSelectedEvent,
        ToolSelectedEvent,
    ]:
        result = serialize_event(cls())
        assert isinstance(result, dict), f"{cls.__name__} serialize failed"
        assert "event_type" in result


# ─── Timestamp sentinel fix ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_converts_float_timestamp_to_iso_string():
    """float timestamp from time.time() must be converted to ISO-8601 string."""
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    event = WorkflowStartedEvent()
    assert isinstance(event.timestamp, float)
    await emitter.emit(event)

    payload = mock_publisher.publish.call_args[0][1]
    ts = payload["timestamp"]
    assert isinstance(ts, str), f"Expected ISO string, got {type(ts)}: {ts}"
    assert "T" in ts, f"Expected ISO-8601 format, got: {ts}"
    assert (
        "+" in ts or ts.endswith("Z") or "+00:00" in ts
    ), f"Expected UTC timezone in: {ts}"


@pytest.mark.asyncio
async def test_emit_already_string_timestamp_is_preserved():
    """if timestamp is already a string, it must not be modified."""
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    event = WorkflowStartedEvent()
    object.__setattr__(event, "timestamp", "2026-01-01T00:00:00+00:00")
    await emitter.emit(event)

    payload = mock_publisher.publish.call_args[0][1]
    assert payload["timestamp"] == "2026-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_emit_none_timestamp_gets_current_utc():
    """None timestamp must be replaced with current UTC ISO string."""
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    event = WorkflowStartedEvent()
    object.__setattr__(event, "timestamp", None)
    await emitter.emit(event)

    payload = mock_publisher.publish.call_args[0][1]
    ts = payload["timestamp"]
    assert isinstance(
        ts, str
    ), f"Expected ISO string for None timestamp, got {type(ts)}"
    assert "T" in ts


# ─────────────────────────────────────────────────────────────────────────────
# Push-gateway dual-publish behavior
# ─────────────────────────────────────────────────────────────────────────────


def test_workflow_event_emitter_accepts_optional_push_gateway_notifier():
    """WorkflowEventEmitter must accept an optional push_gateway_notifier kwarg."""
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    notifier = AsyncMock()
    emitter = WorkflowEventEmitter(
        publisher=mock_publisher, push_gateway_notifier=notifier
    )
    assert emitter is not None


def test_workflow_event_emitter_push_gateway_notifier_uses_protocol_annotation():
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    annotation = get_type_hints(WorkflowEventEmitter.__init__)["push_gateway_notifier"]
    assert PushGatewayNotifierProtocol in get_args(annotation)


def test_workflow_event_emitter_without_push_gateway_notifier_still_works():
    """Existing WorkflowEventEmitter(publisher=...) construction works unchanged."""
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)
    assert emitter is not None


@pytest.mark.asyncio
async def test_emit_calls_push_gateway_notifier_with_conv_id_as_key():
    """emit() must call push_gateway_notifier.send_notification(key=conv_id, ...) when conv_id present."""
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    notifier = AsyncMock()
    emitter = WorkflowEventEmitter(
        publisher=mock_publisher, push_gateway_notifier=notifier
    )

    event = NodeUpdatedEvent(conv_id="conv-abc", correlation_id="corr-123")
    await emitter.emit(event)

    notifier.send_notification.assert_called_once()
    kwargs = notifier.send_notification.call_args.kwargs
    assert kwargs["key"] == "conv-abc"
    assert kwargs["data"]["conv_id"] == "conv-abc"


@pytest.mark.asyncio
async def test_emit_push_gateway_uses_is_final_false_for_non_terminal_events():
    """Non-terminal events must use is_final=False for push gateway notification."""
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    notifier = AsyncMock()
    emitter = WorkflowEventEmitter(
        publisher=mock_publisher, push_gateway_notifier=notifier
    )

    event = NodeUpdatedEvent(conv_id="conv-abc", correlation_id="corr-123")
    await emitter.emit(event)

    kwargs = notifier.send_notification.call_args.kwargs
    assert kwargs["is_final"] is False


@pytest.mark.asyncio
async def test_emit_push_gateway_uses_is_final_true_for_workflow_completed():
    """WorkflowCompletedEvent must use is_final=True for push gateway notification."""
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowCompletedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    notifier = AsyncMock()
    emitter = WorkflowEventEmitter(
        publisher=mock_publisher, push_gateway_notifier=notifier
    )

    event = WorkflowCompletedEvent(conv_id="conv-123", correlation_id="corr-123")
    await emitter.emit(event)

    kwargs = notifier.send_notification.call_args.kwargs
    assert kwargs["is_final"] is True


@pytest.mark.asyncio
async def test_emit_push_gateway_uses_is_final_true_for_workflow_failed():
    """WorkflowFailedEvent must use is_final=True for push gateway notification."""
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowFailedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    notifier = AsyncMock()
    emitter = WorkflowEventEmitter(
        publisher=mock_publisher, push_gateway_notifier=notifier
    )

    event = WorkflowFailedEvent(conv_id="conv-failed", correlation_id="corr-456")
    await emitter.emit(event)

    kwargs = notifier.send_notification.call_args.kwargs
    assert kwargs["is_final"] is True


@pytest.mark.asyncio
async def test_emit_skips_push_gateway_when_conv_id_missing():
    """emit() must skip push gateway notification when conv_id is blank."""
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    notifier = AsyncMock()
    emitter = WorkflowEventEmitter(
        publisher=mock_publisher, push_gateway_notifier=notifier
    )

    event = NodeUpdatedEvent(conv_id="", correlation_id="corr-123")
    await emitter.emit(event)

    notifier.send_notification.assert_not_called()
    mock_publisher.publish.assert_called_once()


@pytest.mark.asyncio
async def test_emit_broker_failure_does_not_prevent_push_gateway():
    """broker publish failure must be logged but must not prevent push gateway delivery."""
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    mock_publisher.publish.side_effect = Exception("broker down")
    notifier = AsyncMock()
    mock_logger = MagicMock()
    emitter = WorkflowEventEmitter(
        publisher=mock_publisher,
        push_gateway_notifier=notifier,
        logger=mock_logger,
    )

    event = NodeUpdatedEvent(conv_id="conv-123", correlation_id="corr-123")
    await emitter.emit(event)

    mock_logger.error.assert_called()
    notifier.send_notification.assert_called_once()


@pytest.mark.asyncio
async def test_emit_push_gateway_failure_does_not_prevent_broker_publish():
    """push gateway notifier failure must be logged/non-fatal and must not prevent broker publish."""
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    notifier = AsyncMock()
    notifier.send_notification.side_effect = Exception("push-gateway down")
    mock_logger = MagicMock()
    emitter = WorkflowEventEmitter(
        publisher=mock_publisher,
        push_gateway_notifier=notifier,
        logger=mock_logger,
    )

    event = NodeUpdatedEvent(conv_id="conv-123", correlation_id="corr-123")
    await emitter.emit(event)

    mock_publisher.publish.assert_called_once()
    mock_logger.error.assert_called()


@pytest.mark.asyncio
async def test_emit_push_gateway_failure_is_non_fatal_without_logger():
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    notifier = AsyncMock()
    notifier.send_notification.side_effect = Exception("push-gateway down")
    emitter = WorkflowEventEmitter(
        publisher=mock_publisher,
        push_gateway_notifier=notifier,
    )

    event = NodeUpdatedEvent(conv_id="conv-123", correlation_id="corr-123")
    await emitter.emit(event)

    mock_publisher.publish.assert_called_once()
    notifier.send_notification.assert_called_once()


@pytest.mark.asyncio
async def test_emit_push_gateway_failure_raises_when_raise_on_error_enabled():
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    notifier = AsyncMock()
    notifier.send_notification.side_effect = RuntimeError("push-gateway down")
    mock_logger = MagicMock()
    emitter = WorkflowEventEmitter(
        publisher=mock_publisher,
        push_gateway_notifier=notifier,
        logger=mock_logger,
        raise_on_error=True,
    )

    event = NodeUpdatedEvent(conv_id="conv-123", correlation_id="corr-123")

    with pytest.raises(RuntimeError, match="push-gateway down"):
        await emitter.emit(event)

    mock_publisher.publish.assert_called_once()
    mock_logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_emit_broker_publish_behavior_unchanged():
    """Broker publish topic resolution and correlation_id key must stay intact."""
    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )

    mock_publisher = AsyncMock()
    notifier = AsyncMock()
    emitter = WorkflowEventEmitter(
        publisher=mock_publisher, push_gateway_notifier=notifier
    )
    request = AgentRequest(message="hi", reply_to="orchestrator.abc.response")

    event = NodeUpdatedEvent(conv_id="conv-xyz", correlation_id="corr-xyz")
    with workflow_event_scope(emitter, request=request, mode="execute"):
        await emitter.emit(event)

    call_args = mock_publisher.publish.call_args
    assert call_args[0][0] == "orchestrator.abc.response.progress"
    assert call_args[1]["key"] == "corr-xyz"


@pytest.mark.asyncio
async def test_emit_push_gateway_notifier_not_called_when_none():
    """When push_gateway_notifier is not provided, broker publish still works normally."""
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    event = NodeUpdatedEvent(conv_id="conv-abc", correlation_id="corr-123")
    await emitter.emit(event)

    mock_publisher.publish.assert_called_once()
