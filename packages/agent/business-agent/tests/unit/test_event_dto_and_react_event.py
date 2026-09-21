from types import SimpleNamespace

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import TaskStatus
from app.layer1_domain.entities.queue_event import (
    EventSource,
    PLAN_EVENTS,
    QueueRequestType,
    REVIEW_EVENTS,
)
from app.layer1_domain.entities.workflow_event import (
    NodeInfo,
    WorkflowEvent,
    WorkflowRun,
    WorkflowStatus,
)
from app.layer2_application.features.main_agent.dto.event import (
    EventDTO,
    NodeInfo as NodeInfoDTO,
    PayloadDTO,
    StepDTO,
)
from app.layer2_application.graphs.nodes import react_event_node as react_event_node_module


def _event_dict(event_type: str) -> dict:
    return {
        "id": "event-1",
        "type": event_type,
        "conv_id": "conv-1",
        "correlation_id": "corr-1",
        "metadata": {"k": "v"},
        "payload": {
            "from": "business-agent",
            "to": "executor.runtime",
            "steps": [
                {
                    "id": "step-1",
                    "execute_by": "tool.tool-1",
                    "goal": "Goal",
                    "status": "pending",
                    "message": "Message",
                    "uploaded_file_ids": [],
                }
            ],
        },
    }


def _workflow_control_plane_event(
    event_type: str = QueueRequestType.WORKFLOW_NODE_COMPLETED.value,
) -> dict:
    return {
        "event_id": "workflow-event-1",
        "timestamp": "2024-01-01T00:00:00Z",
        "source": EventSource.WORKFLOW_CONTROL_PLANE,
        "root_run_id": "conv-1",
        "depth": 0,
        "metadata": {"message": "Need approval"},
        "run_id": "run-1",
        "workflow_id": "workflow-1",
        "node_id": "node-1",
        "status": "waiting",
        "_event": event_type,
        "_run": {"status": "running", "correlation_id": "corr-1"},
        "_node": {"name": "Approve", "node_type": "human"},
    }


def test_step_dto_to_entity_maps_fields():
    dto = StepDTO(
        id="step-1",
        execute_by="tool.tool-1",
        next_step_id="step-2",
        goal="Goal",
        status=TaskStatus.PENDING,
        message="Message",
        uploaded_file_ids=["file-1"],
        input_data={"x": 1},
        output_data={"y": 2},
    )

    entity = EventDTO.step_dto_to_entity(dto)

    assert entity.id == "step-1"
    assert entity.execute_by == "tool.tool-1"
    assert entity.input_data == {"x": 1}


def test_node_info_dto_to_entity_returns_none_for_none():
    assert EventDTO.node_info_dto_to_entity(None) is None


def test_node_info_dto_to_entity_maps_fields():
    dto = NodeInfoDTO(
        run_id="run-1",
        node_id="node-1",
        node_name="Node",
        status="waiting",
        message="Need input",
    )

    entity = EventDTO.node_info_dto_to_entity(dto)

    assert entity is not None
    assert entity.run_id == "run-1"
    assert entity.node_name == "Node"
    assert entity.message == "Need input"


def test_payload_dto_to_entity_maps_steps_and_node_info():
    payload = PayloadDTO(
        from_="business-agent",
        to="executor.runtime",
        steps=[
            StepDTO(
                id="step-1",
                execute_by="tool.tool-1",
                goal="Goal",
                status=TaskStatus.PENDING,
                message="Message",
                uploaded_file_ids=[],
                input_data={"x": 1},
                output_data={"y": 2},
            )
        ],
        node_info=NodeInfoDTO(run_id="run-1", node_id="node-1", node_name="Node"),
    )

    entity = EventDTO.payload_dto_to_entity(payload)

    assert entity.from_ == "business-agent"
    assert entity.workflow_id is None
    assert entity.node_info is not None
    assert entity.node_info.node_id == "node-1"
    assert entity.steps[0].id == "step-1"


# def test_event_dto_to_entity_maps_payload_and_metadata():
#     dto = EventDTO.from_dict(_event_dict(QueueRequestType.SUB_USER_CHAT.value))

#     entity = dto.to_entity()

#     assert entity.message_id == "event-1"
#     assert entity.conv_id == "conv-1"
#     assert entity.metadata == {"k": "v"}
#     assert entity.payload is not None
#     assert entity.payload.steps[0].execute_by == "tool.tool-1"


def test_workflow_event_to_dto_maps_run_and_node_data():
    event = WorkflowEvent(
        event_id="workflow-event-1",
        timestamp="2024-01-01T00:00:00Z",
        source=EventSource.WORKFLOW_CONTROL_PLANE,
        root_run_id="conv-1",
        depth=0,
        metadata={"message": "Need approval"},
        run_id="run-1",
        workflow_id="workflow-1",
        node_id="node-1",
        status=WorkflowStatus.STARTED,
        event_type=QueueRequestType.WORKFLOW_NODE_COMPLETED.value,
        run=WorkflowRun(status="running", correlation_id="corr-1"),
        node=NodeInfo(name="Approve", node_type="human"),
    )

    dto = EventDTO.workflow_event_to_dto(event)

    assert dto.id == "workflow-event-1"
    assert dto.type == QueueRequestType.WORKFLOW_NODE_COMPLETED
    assert dto.conv_id == ""
    assert dto.correlation_id == "corr-1"
    assert dto.payload is not None
    assert dto.payload.workflow is not None
    assert dto.payload.node_info is not None
    assert dto.payload.workflow.workflow_id == "workflow-1"
    assert dto.payload.node_info.node_name == "Approve"


def test_workflow_event_to_dto_handles_missing_run_and_node():
    event = WorkflowEvent(
        event_id="workflow-event-1",
        timestamp="2024-01-01T00:00:00Z",
        source=EventSource.WORKFLOW_CONTROL_PLANE,
        root_run_id="conv-1",
        depth=0,
        event_type=QueueRequestType.CONVERSATION_PLAN_CREATED.value,
    )

    dto = EventDTO.workflow_event_to_dto(event)

    assert dto.correlation_id == ""
    assert dto.payload is not None
    assert dto.payload.node_info is None


@pytest.mark.asyncio
async def test_react_event_node_requires_non_null_event_data():
    with pytest.raises(ValueError, match="Event data is missing"):
        await react_event_node_module.react_event_node({"parameters": {"event": None}})


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", [event.value for event in PLAN_EVENTS])
async def test_react_event_node_routes_plan_events(monkeypatch, event_type):
    captured = {}

    async def execute(event):
        captured["event"] = event
        return {"handled": event.type.value}

    monkeypatch.setattr(
        react_event_node_module,
        "get_usecase",
        lambda name: SimpleNamespace(execute=execute, name=name),
    )

    result = await react_event_node_module.react_event_node(
        {"parameters": {"event": _event_dict(event_type)}}
    )

    assert result == {"handled": event_type}
    assert captured["event"].type.value == event_type


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", [event.value for event in REVIEW_EVENTS])
async def test_react_event_node_routes_review_events(monkeypatch, event_type):
    captured = {}

    async def execute(event):
        captured["event"] = event
        return {"handled": event.type.value}

    def fake_get_usecase(name):
        captured["name"] = name
        return SimpleNamespace(execute=execute)

    monkeypatch.setattr(react_event_node_module, "get_usecase", fake_get_usecase)

    result = await react_event_node_module.react_event_node(
        {"parameters": {"event": _event_dict(event_type)}}
    )

    assert result == {"handled": event_type}
    assert captured["name"] == "review_plan"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        QueueRequestType.WORKFLOW_NODE_COMPLETED.value,
        QueueRequestType.WORKFLOW_NODE_DISPATCHED.value,
    ],
)
async def test_react_event_node_converts_workflow_control_plane_events(
    monkeypatch, event_type
):
    captured = {}

    async def execute(event):
        captured["event"] = event
        return {"handled": event.type.value}

    monkeypatch.setattr(
        react_event_node_module,
        "get_usecase",
        lambda name: SimpleNamespace(execute=execute),
    )

    result = await react_event_node_module.react_event_node(
        {"parameters": {"event": _workflow_control_plane_event(event_type)}}
    )

    assert result == {"handled": event_type}
    assert captured["event"].conv_id == ""
    assert captured["event"].payload is not None
    assert captured["event"].payload.workflow is not None
    assert captured["event"].payload.node_info is not None
    assert captured["event"].payload.workflow.workflow_id == "workflow-1"
    assert captured["event"].payload.workflow.root_run_id == "conv-1"
    assert captured["event"].payload.node_info.node_name == "Approve"


@pytest.mark.asyncio
async def test_react_event_node_rejects_valid_but_unsupported_event_type(monkeypatch):
    monkeypatch.setattr(
        react_event_node_module,
        "get_usecase",
        lambda name: SimpleNamespace(execute=None),
    )

    with pytest.raises(ValueError, match="Unsupported event type"):
        await react_event_node_module.react_event_node(
            {
                "parameters": {
                    "event": _event_dict(QueueRequestType.WORKFLOW_NODE_OUTPUT_PRODUCED.value)
                }
            }
        )