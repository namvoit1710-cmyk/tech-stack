from typing import Any, get_type_hints
from unittest.mock import AsyncMock

import pytest

from agent_sdk.layer1_domain.entities.conversation_metadata import ConversationMetadata


def test_workflow_event_emitter_protocol_accepts_general_sdk_event_dataclasses():
    from agent_sdk.layer2_application.interfaces.workflow_event_emitter import (
        IWorkflowEventEmitter,
    )

    assert get_type_hints(IWorkflowEventEmitter.emit)["event"] is Any


@pytest.mark.asyncio
async def test_emit_ui_event_enriches_chat_payload_from_scope_context():
    from agent_sdk.layer1_domain.entities.ui_events import (
        ChatResponseEvent,
        TextPayload,
    )
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
    )
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )

    publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=publisher)
    request = ExecuteAgentInput(
        message="hello",
        conv_id="main_ui_1",
        correlation_id="corr-ui-1",
        reply_to="agent.reply",
        metadata=ConversationMetadata(
            main_conv_id="main_ui_1",
            sub_conv_ids=["sub_ui_1"],
            uploaded_file_ids=["file-ui-1"],
        ),
        uploaded_file_ids=["file-ui-1"],
    )

    with workflow_event_scope(emitter, request=request, mode="execute"):
        await emitter.emit_ui_event(ChatResponseEvent(payload=TextPayload(text="done")))

    topic, payload = publisher.publish.call_args.args[:2]
    assert topic == "agent.reply.progress"
    assert payload["event_type"] == "chat:response"
    assert payload["conv_id"] == "main_ui_1"
    assert payload["correlation_id"] == "corr-ui-1"
    assert isinstance(payload["event_id"], str)
    assert isinstance(payload["timestamp"], str)
    assert payload["metadata"] == {
        "main_conv_id": "main_ui_1",
        "sub_conv_ids": ["sub_ui_1"],
        "uploaded_file_ids": ["file-ui-1"],
    }
    assert payload["payload"] == {
        "type": "text",
        "content": "done",
        "status": "processing",
    }
    assert "type" not in payload
    assert "conversation_id" not in payload
    assert "uploaded_file_ids" not in payload


@pytest.mark.asyncio
async def test_chat_toggle_events_include_empty_payload_objects():
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=publisher)

    await emitter.emit_chat_enabled(conversation_id="main_chat_1")
    await emitter.emit_chat_disabled(conversation_id="main_chat_1")

    first_payload = publisher.publish.call_args_list[0].args[1]
    second_payload = publisher.publish.call_args_list[1].args[1]
    assert first_payload["event_type"] == "chat:enabled"
    assert second_payload["event_type"] == "chat:disabled"
    assert first_payload["conv_id"] == "main_chat_1"
    assert second_payload["conv_id"] == "main_chat_1"
    assert first_payload["payload"] == {}
    assert second_payload["payload"] == {}


@pytest.mark.asyncio
async def test_emit_orchestration_event_uses_envelope_type_as_default_topic():
    from agent_sdk.layer1_domain.entities.orchestration_events import (
        create_agent_plan_success_event,
    )
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=publisher)

    await emitter.emit_orchestration_event(
        create_agent_plan_success_event("main_orch_1", {"status": "ok"})
    )

    topic, payload = publisher.publish.call_args.args[:2]
    assert topic == "agent.plan.success"
    assert payload["type"] == "agent.plan.success"
    assert payload["conversation_id"] == "main_orch_1"
