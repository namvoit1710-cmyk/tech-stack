"""Tests for the GetSocketioConfig use case."""
import dataclasses
import pytest

from app.layer2_application.features.get_socketio_config.use_cases.get_socketio_config_use_case import (
    GetSocketioConfigUseCase,
    GetSocketioConfigCommand,
    GetSocketioConfigResult,
    _EVENT_DATACLASS_MAP,
)


@pytest.fixture
def usecase():
    return GetSocketioConfigUseCase(logger=None)


async def test_returns_all_events(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    assert len(result.events) == 17


async def test_no_error(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    assert result.error == ""


async def test_run_category_events(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    run_events = [e for e in result.events if e.category == "run"]
    event_names = {e.event for e in run_events}
    assert event_names == {"run.status"}


async def test_node_lifecycle_category_events(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    node_events = [e for e in result.events if e.category == "node"]
    event_names = {e.event for e in node_events}
    assert {
        "node.dispatched", "node.completed", "node.failed", "node.cancelled",
    } <= event_names


async def test_input_category_events(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    events = [e for e in result.events if e.category == "input"]
    assert {e.event for e in events} == {"input.requested", "input.submitted"}


async def test_node_category_events(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    events = [e for e in result.events if e.category == "node"]
    assert {e.event for e in events} == {
        "node.dispatched",
        "node.completed",
        "node.failed",
        "node.cancelled",
        "loop.iteration.completed",
        "node.approval_flow.result",
    }


async def test_child_run_category_events(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    events = [e for e in result.events if e.category == "child_run"]
    assert {e.event for e in events} == {
        "child_run.started", "child_run.completed",
        "child_run.failed", "child_run.cancelled",
    }


async def test_context_category_events(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    events = [e for e in result.events if e.category == "context"]
    assert {e.event for e in events} == {"context.updated"}


async def test_child_run_events_use_parent_run_room(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    child_events = [e for e in result.events if e.category == "child_run"]
    for evt in child_events:
        assert evt.room == "run:{parent_run_id}"


async def test_non_child_events_use_run_room(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    non_child = [e for e in result.events if e.category != "child_run"]
    for evt in non_child:
        assert evt.room == "run:{run_id}"


async def test_room_definitions(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    assert len(result.rooms) == 1
    patterns = {r.room_pattern for r in result.rooms}
    assert patterns == {"run:{run_id}"}

    run_room = next(r for r in result.rooms if r.room_pattern == "run:{run_id}")
    assert run_room.join_event == "join:run"
    assert run_room.leave_event == "leave:run"


async def test_meta_fields(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    names = {f.name for f in result.meta_fields}
    assert "_event" in names
    assert "_timestamp" in names
    assert "event_id" in names
    assert "timestamp" in names
    assert "source" in names


async def test_event_fields_match_dataclass(usecase):
    """Each event's fields should match its domain event dataclass (excluding base fields)."""
    result = await usecase.execute(GetSocketioConfigCommand())
    base_names = {f.name for f in dataclasses.fields(
        __import__("app.layer1_domain.events", fromlist=["DomainEvent"]).DomainEvent
    )}

    event_map = {e.event: e for e in result.events}
    for event_name, event_cls in _EVENT_DATACLASS_MAP.items():
        expected_fields = {f.name for f in dataclasses.fields(event_cls) if f.name not in base_names}
        actual_fields = {f.name for f in event_map[event_name].fields}
        assert actual_fields == expected_fields, (
            f"Field mismatch for {event_name}: expected {expected_fields}, got {actual_fields}"
        )


async def test_every_event_has_description(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    for evt in result.events:
        assert evt.description, f"Missing description for {evt.event}"


async def test_result_serializable(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    d = dataclasses.asdict(result)
    assert isinstance(d, dict)
    assert len(d["events"]) == 17
    assert len(d["rooms"]) == 1


async def test_approval_flow_event_schema_is_exposed(usecase):
    result = await usecase.execute(GetSocketioConfigCommand())
    event = next(e for e in result.events if e.event == "node.approval_flow.result")
    assert event.category == "node"
    assert {field.name for field in event.fields} == {
        "run_id",
        "workflow_id",
        "node_id",
        "node_name",
        "instruction",
        "change_request_id",
        "nodes",
        "edges",
        "output_port",
        "error",
    }
