import dataclasses


def test_serialize_event_supports_ui_event_dataclasses():
    from agent_sdk.layer1_domain.entities.ui_events import (
        ChatResponseEvent,
        TextPayload,
    )
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        serialize_event,
    )

    event = ChatResponseEvent(
        conversation_id="main_compat_1",
        payload=TextPayload(text="hello"),
    )

    payload = serialize_event(event)
    assert payload != dataclasses.asdict(event)
    assert isinstance(payload["event_id"], str)
    assert payload["event_id"]
    assert payload["event_type"] == "chat:response"
    assert isinstance(payload["timestamp"], str)
    assert payload["conv_id"] == "main_compat_1"
    assert "type" not in payload
    assert "conversation_id" not in payload
    assert payload["payload"]["type"] == "text"
    assert payload["payload"]["content"] == "hello"


def test_serialize_event_supports_orchestration_event_dataclasses():
    from agent_sdk.layer1_domain.entities.orchestration_events import (
        TaskDefinition,
        create_agent_plan_created_event,
    )
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        serialize_event,
    )

    payload = serialize_event(
        create_agent_plan_created_event(
            "main_compat_2",
            TaskDefinition(task_id="task-1", name="Review"),
        )
    )

    assert payload["type"] == "agent.plan.created"
    assert payload["conversation_id"] == "main_compat_2"
    assert payload["payload"]["task"]["task_id"] == "task-1"


def test_serialize_event_keeps_legacy_workflow_event_shape_unchanged():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        serialize_event,
    )

    payload = serialize_event(WorkflowStartedEvent(conv_id="legacy-shape-1"))

    assert payload["event_type"] == "WORKFLOW_STARTED"
    assert payload["conv_id"] == "legacy-shape-1"
