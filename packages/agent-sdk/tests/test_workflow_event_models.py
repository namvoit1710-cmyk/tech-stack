"""
Tests for implement all 15 Workflow Event Models.
"""

import dataclasses
import time
import uuid
from dataclasses import fields


def assert_is_uuid_string(value: str) -> None:
    uuid.UUID(value)


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1: Domain entity – WorkflowEvent base + 15 event classes
# ─────────────────────────────────────────────────────────────────────────────


def test_workflow_event_base_class_exists():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowEvent

    assert dataclasses.is_dataclass(WorkflowEvent)


def test_workflow_event_base_has_required_fields():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowEvent

    field_names = {f.name for f in fields(WorkflowEvent)}
    expected = {
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
    assert expected.issubset(field_names)


def test_workflow_event_base_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowEvent

    evt = WorkflowEvent(event_id="X", event_type="X")
    assert evt.message == ""
    assert evt.conv_id == ""
    uuid.UUID(evt.correlation_id)
    assert isinstance(evt.timestamp, float)
    assert evt.workflow_id == ""
    assert evt.node_id == ""
    assert evt.data == {}


def test_all_15_event_classes_exist():
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
        assert dataclasses.is_dataclass(cls), f"{cls.__name__} should be a dataclass"


def test_event_type_constants_exist():
    from agent_sdk.layer1_domain.entities.workflow_event import (
        EVENT_AGENT_SELECTED,
        EVENT_INPUT_UPDATED,
        EVENT_INPUT_VALIDATING,
        EVENT_NODE_COMPLETED,
        EVENT_NODE_DATA_INITIALIZED,
        EVENT_NODE_STARTED,
        EVENT_NODE_UPDATED,
        EVENT_NODE_WAITING_USER,
        EVENT_TOOL_SELECTED,
        EVENT_UI_RENDER_REQUEST,
        EVENT_UNSUPPORTED_FEATURE,
        EVENT_WORKFLOW_COMPLETED,
        EVENT_WORKFLOW_FAILED,
        EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST,
        EVENT_WORKFLOW_STARTED,
    )

    assert EVENT_WORKFLOW_STARTED == "WORKFLOW_STARTED"
    assert EVENT_UI_RENDER_REQUEST == "UI_RENDER_REQUEST"
    assert (
        EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST == "WORKFLOW_GUIDELINE_RENDER_REQUEST"
    )
    assert EVENT_NODE_STARTED == "NODE_STARTED"
    assert EVENT_INPUT_VALIDATING == "INPUT_VALIDATING"
    assert EVENT_NODE_WAITING_USER == "NODE_WAITING_USER"
    assert EVENT_INPUT_UPDATED == "INPUT_UPDATED"
    assert EVENT_NODE_COMPLETED == "NODE_COMPLETED"
    assert EVENT_WORKFLOW_COMPLETED == "WORKFLOW_COMPLETED"
    assert EVENT_WORKFLOW_FAILED == "WORKFLOW_FAILED"
    assert EVENT_UNSUPPORTED_FEATURE == "UNSUPPORTED_FEATURE"
    assert EVENT_NODE_UPDATED == "NODE_UPDATED"
    assert EVENT_NODE_DATA_INITIALIZED == "NODE_DATA_INITIALIZED"
    assert EVENT_AGENT_SELECTED == "AGENT_SELECTED"
    assert EVENT_TOOL_SELECTED == "TOOL_SELECTED"


def test_workflow_started_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent

    evt = WorkflowStartedEvent()
    assert evt.event_id == "WORKFLOW_STARTED"
    assert evt.event_type == "WORKFLOW_STARTED"
    assert evt.message == "Workflow started"


def test_ui_render_request_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import UiRenderRequestEvent

    evt = UiRenderRequestEvent()
    assert evt.event_id == "UI_RENDER_REQUEST"
    assert evt.event_type == "UI_RENDER_REQUEST"
    assert (
        evt.message == "The Workflow interface will be rendered in right side of chat."
    )
    assert evt.data.get("ui_yaml_blocks") == "td/tr"


def test_workflow_guideline_render_request_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import (
        WorkflowGuidelineRenderRequestEvent,
    )

    evt = WorkflowGuidelineRenderRequestEvent()
    assert evt.event_id == "WORKFLOW_GUIDELINE_RENDER_REQUEST"
    assert evt.event_type == "WORKFLOW_GUIDELINE_RENDER_REQUEST"


def test_node_started_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import NodeStartedEvent

    evt = NodeStartedEvent()
    assert evt.event_id == "NODE_STARTED"
    assert evt.event_type == "NODE_STARTED"
    assert evt.message == "Node execution started"


def test_input_validating_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import InputValidatingEvent

    evt = InputValidatingEvent()
    assert evt.event_id == "INPUT_VALIDATING"
    assert evt.event_type == "INPUT_VALIDATING"
    assert evt.message == "Validating input"


def test_node_waiting_user_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import NodeWaitingUserEvent

    evt = NodeWaitingUserEvent()
    assert evt.event_id == "NODE_WAITING_USER"
    assert evt.event_type == "NODE_WAITING_USER"
    assert "missing_required_keys" in evt.data
    assert "filled_required_keys" in evt.data


def test_input_updated_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import InputUpdatedEvent

    evt = InputUpdatedEvent()
    assert evt.event_id == "INPUT_UPDATED"
    assert evt.event_type == "INPUT_UPDATED"
    assert "missing_required_keys" in evt.data
    assert "filled_required_keys" in evt.data


def test_node_completed_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import NodeCompletedEvent

    evt = NodeCompletedEvent()
    assert evt.event_id == "NODE_COMPLETED"
    assert evt.event_type == "NODE_COMPLETED"


def test_workflow_completed_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowCompletedEvent

    evt = WorkflowCompletedEvent()
    assert evt.event_id == "WORKFLOW_COMPLETED"
    assert evt.event_type == "WORKFLOW_COMPLETED"
    assert evt.message == "Workflow completed"


def test_workflow_failed_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowFailedEvent

    evt = WorkflowFailedEvent()
    assert evt.event_id == "WORKFLOW_FAILED"
    assert evt.event_type == "WORKFLOW_FAILED"
    assert evt.message == "Workflow failed"


def test_unsupported_feature_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import UnsupportedFeatureEvent

    evt = UnsupportedFeatureEvent()
    assert evt.event_id == "UNSUPPORTED_FEATURE"
    assert evt.event_type == "UNSUPPORTED_FEATURE"
    assert evt.message == "This feature is not supported in the current version."


def test_node_updated_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent

    evt = NodeUpdatedEvent()
    assert evt.event_id == "NODE_UPDATED"
    assert evt.event_type == "NODE_UPDATED"
    assert evt.message == "Node was updated."
    assert evt.data.get("json_data") == "{}"
    assert evt.data.get("file_id") == ""


def test_node_data_initialized_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import NodeDataInitializedEvent

    evt = NodeDataInitializedEvent()
    assert evt.event_id == "NODE_DATA_INITIALIZED"
    assert evt.event_type == "NODE_DATA_INITIALIZED"
    assert evt.message == "Update data for nodes."
    assert isinstance(evt.data.get("nodes"), list)


def test_agent_selected_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import AgentSelectedEvent

    evt = AgentSelectedEvent()
    assert evt.event_id == "AGENT_SELECTED"
    assert evt.event_type == "AGENT_SELECTED"


def test_tool_selected_event_defaults():
    from agent_sdk.layer1_domain.entities.workflow_event import ToolSelectedEvent

    evt = ToolSelectedEvent()
    assert evt.event_id == "TOOL_SELECTED"
    assert evt.event_type == "TOOL_SELECTED"


def test_workflow_started_event_has_fixed_event_id_constant():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent

    evt1 = WorkflowStartedEvent()
    evt2 = WorkflowStartedEvent()
    assert evt1.event_id == evt2.event_id == "WORKFLOW_STARTED"
    assert evt1.event_type == evt2.event_type == "WORKFLOW_STARTED"


def test_all_events_inherit_from_workflow_event():
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
        WorkflowEvent,
        WorkflowFailedEvent,
        WorkflowGuidelineRenderRequestEvent,
        WorkflowStartedEvent,
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
        assert issubclass(
            cls, WorkflowEvent
        ), f"{cls.__name__} must inherit from WorkflowEvent"


# ─────────────────────────────────────────────────────────────────────────────
# Public API exports: WorkflowEvent and event-type constants
# ─────────────────────────────────────────────────────────────────────────────


def test_workflow_event_exported_from_agent_sdk():
    from agent_sdk import WorkflowEvent

    assert dataclasses.is_dataclass(WorkflowEvent)


def test_event_constants_exported_from_agent_sdk():
    from agent_sdk import (
        EVENT_AGENT_SELECTED,
        EVENT_TOOL_SELECTED,
        EVENT_WORKFLOW_STARTED,
    )

    assert EVENT_WORKFLOW_STARTED == "WORKFLOW_STARTED"
    assert EVENT_AGENT_SELECTED == "AGENT_SELECTED"
    assert EVENT_TOOL_SELECTED == "TOOL_SELECTED"


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2: Application layer DTOs – event_models.py
# ─────────────────────────────────────────────────────────────────────────────


def test_event_models_module_exists():
    from agent_sdk.layer2_application.features.events.use_cases import event_models

    assert event_models is not None


def test_event_dto_base_exists():
    from agent_sdk.layer2_application.features.events.use_cases.event_models import (
        EventDto,
    )

    assert dataclasses.is_dataclass(EventDto)


def test_event_dto_has_required_fields():
    from agent_sdk.layer2_application.features.events.use_cases.event_models import (
        EventDto,
    )

    field_names = {f.name for f in fields(EventDto)}
    expected = {
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
    assert expected.issubset(field_names)


def test_event_dto_from_domain_event():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent
    from agent_sdk.layer2_application.features.events.use_cases.event_models import (
        EventDto,
    )

    domain_evt = WorkflowStartedEvent(conv_id="conv-123", workflow_id="wf-1")
    dto = EventDto.from_domain(domain_evt)
    assert dto.event_type == "WORKFLOW_STARTED"
    assert dto.conv_id == "conv-123"
    assert dto.workflow_id == "wf-1"
    assert dto.event_id == domain_evt.event_id


def test_event_dto_defaults():
    import uuid

    from agent_sdk.layer2_application.features.events.use_cases.event_models import (
        EventDto,
    )

    dto = EventDto(event_id="test", event_type="test")
    uuid.UUID(dto.correlation_id)
    assert isinstance(dto.timestamp, float)


def test_all_15_event_dtos_exist():
    from agent_sdk.layer2_application.features.events.use_cases.event_models import (
        AgentSelectedEventDto,
        InputUpdatedEventDto,
        InputValidatingEventDto,
        NodeCompletedEventDto,
        NodeDataInitializedEventDto,
        NodeStartedEventDto,
        NodeUpdatedEventDto,
        NodeWaitingUserEventDto,
        ToolSelectedEventDto,
        UiRenderRequestEventDto,
        UnsupportedFeatureEventDto,
        WorkflowCompletedEventDto,
        WorkflowFailedEventDto,
        WorkflowGuidelineRenderRequestEventDto,
        WorkflowStartedEventDto,
    )

    for cls in [
        WorkflowStartedEventDto,
        UiRenderRequestEventDto,
        WorkflowGuidelineRenderRequestEventDto,
        NodeStartedEventDto,
        InputValidatingEventDto,
        NodeWaitingUserEventDto,
        InputUpdatedEventDto,
        NodeCompletedEventDto,
        WorkflowCompletedEventDto,
        WorkflowFailedEventDto,
        UnsupportedFeatureEventDto,
        NodeUpdatedEventDto,
        NodeDataInitializedEventDto,
        AgentSelectedEventDto,
        ToolSelectedEventDto,
    ]:
        assert dataclasses.is_dataclass(cls), f"{cls.__name__} should be a dataclass"


# ─────────────────────────────────────────────────────────────────────────────
# Layer 3: Pydantic models – dtos/events.py
# ─────────────────────────────────────────────────────────────────────────────


def test_pydantic_events_module_exists():
    from agent_sdk.layer3_adapters.presenters.agent_server.dtos import events

    assert events is not None


def test_pydantic_workflow_event_base_exists():
    from pydantic import BaseModel

    from agent_sdk.layer3_adapters.presenters.agent_server.dtos.events import (
        WorkflowEventPydantic,
    )

    assert issubclass(WorkflowEventPydantic, BaseModel)


def test_pydantic_workflow_event_fields():
    from agent_sdk.layer3_adapters.presenters.agent_server.dtos.events import (
        WorkflowEventPydantic,
    )

    field_names = set(WorkflowEventPydantic.model_fields.keys())
    expected = {
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
    assert expected.issubset(field_names)


def test_pydantic_workflow_event_defaults():
    from agent_sdk.layer3_adapters.presenters.agent_server.dtos.events import (
        WorkflowEventPydantic,
    )

    evt = WorkflowEventPydantic(event_id="X", event_type="X")
    assert evt.message == ""
    assert evt.conv_id == ""
    uuid.UUID(evt.correlation_id)
    assert isinstance(evt.timestamp, float)
    assert evt.workflow_id == ""
    assert evt.node_id == ""
    assert evt.data == {}


def test_all_15_pydantic_event_models_exist():
    from pydantic import BaseModel

    from agent_sdk.layer3_adapters.presenters.agent_server.dtos.events import (
        AgentSelectedPydantic,
        InputUpdatedPydantic,
        InputValidatingPydantic,
        NodeCompletedPydantic,
        NodeDataInitializedPydantic,
        NodeStartedPydantic,
        NodeUpdatedPydantic,
        NodeWaitingUserPydantic,
        ToolSelectedPydantic,
        UiRenderRequestPydantic,
        UnsupportedFeaturePydantic,
        WorkflowCompletedPydantic,
        WorkflowFailedPydantic,
        WorkflowGuidelineRenderRequestPydantic,
        WorkflowStartedPydantic,
    )

    for cls in [
        WorkflowStartedPydantic,
        UiRenderRequestPydantic,
        WorkflowGuidelineRenderRequestPydantic,
        NodeStartedPydantic,
        InputValidatingPydantic,
        NodeWaitingUserPydantic,
        InputUpdatedPydantic,
        NodeCompletedPydantic,
        WorkflowCompletedPydantic,
        WorkflowFailedPydantic,
        UnsupportedFeaturePydantic,
        NodeUpdatedPydantic,
        NodeDataInitializedPydantic,
        AgentSelectedPydantic,
        ToolSelectedPydantic,
    ]:
        assert issubclass(
            cls, BaseModel
        ), f"{cls.__name__} must be a Pydantic BaseModel"


def test_pydantic_event_from_domain():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent
    from agent_sdk.layer3_adapters.presenters.agent_server.dtos.events import (
        WorkflowStartedPydantic,
    )

    domain_evt = WorkflowStartedEvent(conv_id="conv-abc", workflow_id="wf-42")
    pydantic_evt = WorkflowStartedPydantic.from_domain(domain_evt)
    assert pydantic_evt.event_type == "WORKFLOW_STARTED"
    assert pydantic_evt.conv_id == "conv-abc"
    assert pydantic_evt.workflow_id == "wf-42"
    assert pydantic_evt.event_id == domain_evt.event_id


def test_pydantic_workflow_started_defaults():
    from agent_sdk.layer3_adapters.presenters.agent_server.dtos.events import (
        WorkflowStartedPydantic,
    )

    evt = WorkflowStartedPydantic()
    assert evt.event_id == evt.event_type == "WORKFLOW_STARTED"
    assert evt.event_type == "WORKFLOW_STARTED"
    assert evt.message == "Workflow started"


def test_pydantic_node_waiting_user_data_structure():
    from agent_sdk.layer3_adapters.presenters.agent_server.dtos.events import (
        NodeWaitingUserPydantic,
    )

    evt = NodeWaitingUserPydantic()
    assert "missing_required_keys" in evt.data
    assert "filled_required_keys" in evt.data


def test_pydantic_node_updated_data_structure():
    from agent_sdk.layer3_adapters.presenters.agent_server.dtos.events import (
        NodeUpdatedPydantic,
    )

    evt = NodeUpdatedPydantic()
    assert evt.data.get("json_data") == "{}"
    assert evt.data.get("file_id") == ""


def test_pydantic_node_data_initialized_data_structure():
    from agent_sdk.layer3_adapters.presenters.agent_server.dtos.events import (
        NodeDataInitializedPydantic,
    )

    evt = NodeDataInitializedPydantic()
    assert isinstance(evt.data.get("nodes"), list)


def test_pydantic_ui_render_request_data_structure():
    from agent_sdk.layer3_adapters.presenters.agent_server.dtos.events import (
        UiRenderRequestPydantic,
    )

    evt = UiRenderRequestPydantic()
    assert evt.data.get("ui_yaml_blocks") == "td/tr"


# ─────────────────────────────────────────────────────────────────────────────
# serialize_event helper: no schema changes
# ─────────────────────────────────────────────────────────────────────────────


def test_serialize_event_helper_exists():
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        serialize_event,
    )

    assert callable(serialize_event)


def test_serialize_event_preserves_all_base_keys():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowStartedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        serialize_event,
    )

    result = serialize_event(WorkflowStartedEvent())
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


def test_serialize_event_does_not_alter_event_type_value():
    from agent_sdk.layer1_domain.entities.workflow_event import NodeUpdatedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        serialize_event,
    )

    result = serialize_event(NodeUpdatedEvent())
    assert result["event_type"] == "NODE_UPDATED"
    assert result["event_id"] == "NODE_UPDATED"


def test_serialize_event_returns_new_dict_not_event():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowCompletedEvent
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        serialize_event,
    )

    event = WorkflowCompletedEvent(conv_id="my-conv")
    result = serialize_event(event)
    assert isinstance(result, dict)
    result["conv_id"] = "mutated"
    assert event.conv_id == "my-conv"


# ─────────────────────────────────────────────────────────────────────────────
# TDD: event_id fixed constants, dynamic correlation_id and timestamp
# ─────────────────────────────────────────────────────────────────────────────


def test_workflow_event_base_correlation_id_is_dynamic_uuid():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowEvent

    evt1 = WorkflowEvent(event_id="X", event_type="X")
    evt2 = WorkflowEvent(event_id="Y", event_type="Y")
    uuid.UUID(evt1.correlation_id)
    uuid.UUID(evt2.correlation_id)
    assert evt1.correlation_id != evt2.correlation_id


def test_workflow_event_base_timestamp_is_unix_float():
    from agent_sdk.layer1_domain.entities.workflow_event import WorkflowEvent

    before = time.time()
    evt = WorkflowEvent(event_id="X", event_type="X")
    after = time.time()
    assert isinstance(evt.timestamp, float)
    assert before <= evt.timestamp <= after


def test_workflow_started_event_id_is_fixed_constant():
    from agent_sdk.layer1_domain.entities.workflow_event import (
        EVENT_WORKFLOW_STARTED,
        WorkflowStartedEvent,
    )

    evt1 = WorkflowStartedEvent()
    evt2 = WorkflowStartedEvent()
    assert evt1.event_id == EVENT_WORKFLOW_STARTED
    assert evt2.event_id == EVENT_WORKFLOW_STARTED


def test_all_event_subclasses_have_fixed_event_id_constants():
    from agent_sdk.layer1_domain.entities.workflow_event import (
        EVENT_AGENT_SELECTED,
        EVENT_INPUT_UPDATED,
        EVENT_INPUT_VALIDATING,
        EVENT_NODE_COMPLETED,
        EVENT_NODE_DATA_INITIALIZED,
        EVENT_NODE_STARTED,
        EVENT_NODE_UPDATED,
        EVENT_NODE_WAITING_USER,
        EVENT_TOOL_SELECTED,
        EVENT_UI_RENDER_REQUEST,
        EVENT_UNSUPPORTED_FEATURE,
        EVENT_WORKFLOW_COMPLETED,
        EVENT_WORKFLOW_FAILED,
        EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST,
        EVENT_WORKFLOW_STARTED,
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

    expected = [
        (WorkflowStartedEvent, EVENT_WORKFLOW_STARTED),
        (UiRenderRequestEvent, EVENT_UI_RENDER_REQUEST),
        (WorkflowGuidelineRenderRequestEvent, EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST),
        (NodeStartedEvent, EVENT_NODE_STARTED),
        (InputValidatingEvent, EVENT_INPUT_VALIDATING),
        (NodeWaitingUserEvent, EVENT_NODE_WAITING_USER),
        (InputUpdatedEvent, EVENT_INPUT_UPDATED),
        (NodeCompletedEvent, EVENT_NODE_COMPLETED),
        (WorkflowCompletedEvent, EVENT_WORKFLOW_COMPLETED),
        (WorkflowFailedEvent, EVENT_WORKFLOW_FAILED),
        (UnsupportedFeatureEvent, EVENT_UNSUPPORTED_FEATURE),
        (NodeUpdatedEvent, EVENT_NODE_UPDATED),
        (NodeDataInitializedEvent, EVENT_NODE_DATA_INITIALIZED),
        (AgentSelectedEvent, EVENT_AGENT_SELECTED),
        (ToolSelectedEvent, EVENT_TOOL_SELECTED),
    ]
    for cls, expected_id in expected:
        evt = cls()
        assert (
            evt.event_id == expected_id
        ), f"{cls.__name__}.event_id should be {expected_id!r}, got {evt.event_id!r}"
