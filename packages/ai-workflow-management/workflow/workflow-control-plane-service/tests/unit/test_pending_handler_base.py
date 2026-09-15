"""Unit tests for ``PendingHandlerBase``'s template method.

A minimal subclass exercises the orchestration: prefill → schema
build → provisioning → task creation → InputRequested event. Each
hook records the keyword args it received so we can verify the
template threads ``ctx`` through them consistently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.events import InputRequested, InputSubmitted, EventTopic
from app.layer2_application.node_handlers.pending_base import PendingHandlerBase
from app.layer1_domain.value_objects import NodeType, RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _StubResolver:
    async def resolve_mapping(self, mapping, variables, type_map=None):
        return dict(mapping)


@dataclass
class _Captured:
    prefill_called: bool = False
    schema_called: bool = False
    schema_received_prefill: dict = field(default_factory=dict)
    provision_called: bool = False
    output_metadata_received_schema: list = field(default_factory=list)
    event_received_instruction: str = ""


class _StubPending(PendingHandlerBase):
    pending_kind: ClassVar[str] = "input"

    def __init__(self, captured: _Captured, provision_value=None) -> None:
        super().__init__(_StubResolver())
        self.captured = captured
        self._provision_value = provision_value

    async def resolve_prefill(self, *, node, run, ctx):
        self.captured.prefill_called = True
        ctx["echo"] = "from_prefill"
        return await super().resolve_prefill(node=node, run=run, ctx=ctx)

    async def build_input_schema(self, *, node, run, workflow, prefilled, ctx):
        self.captured.schema_called = True
        self.captured.schema_received_prefill = dict(prefilled)
        ctx["schema_ran"] = True
        return [{"key": "name", "type": "string"}]

    async def provision_artifacts(self, *, node, run, input_schema, prefilled, ctx):
        self.captured.provision_called = True
        return self._provision_value

    def build_output_metadata(self, *, node, run, resolved_input, prefilled,
                              input_schema, ctx):
        self.captured.output_metadata_received_schema = list(input_schema)
        return {
            "node_type": "STUB",
            "input_schema": input_schema,
            "instruction": ctx.get("echo", ""),
            "prefilled": prefilled,
        }

    def build_input_requested_event(self, *, task, run, node, instruction,
                                    prefilled, input_schema, ctx):
        self.captured.event_received_instruction = instruction
        return InputRequested(
            task_id=task.id,
            run_id=run.id,
            workflow_id=run.workflow_id,
            node_id=node.id,
            instruction=instruction,
            pending_kind=self.pending_kind,
        )

    def build_completion_event(self, task, run, node, action):
        payload = InputSubmitted(
            task_id=task.id, run_id=task.run_id,
            workflow_id=run.workflow_id, node_id=node.id,
            pending_kind=self.pending_kind,
        )
        return EventTopic.INPUT_SUBMITTED, payload


class _StubTaskFactory:
    def create_task(self, node, run, resolved_input=None):
        task = Task(
            id=f"t-{node.id}",
            run_id=run.id,
            node_id=node.id,
            worker_type="",
            status=TaskStatus.PENDING,
            created_at="2026-05-21T00:00:00Z",
        )
        task.resolved_input = resolved_input
        return task


def _make_node():
    return WorkflowNode(
        id="n1", name="Stub",
        node_type=NodeType.INPUT,
        config={"instruction": "Please respond"},
        input_binding={"name": "literal"},
    )


def _make_run():
    return WorkflowRun(
        id="r-1", workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(variables={}),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pending_template_orchestrates_hooks_in_order():
    captured = _Captured()
    handler = _StubPending(captured)

    result = await handler.handle(
        node=_make_node(), run=_make_run(), workflow=None,
        topology=None, completed_task=None, task_factory=_StubTaskFactory(),
    )

    assert captured.prefill_called
    assert captured.schema_called
    assert captured.provision_called
    assert captured.schema_received_prefill == {"name": "literal"}
    assert captured.output_metadata_received_schema == [
        {"key": "name", "type": "string"}
    ]
    assert captured.event_received_instruction == "Please respond"

    assert result.completion_mode == "pending"
    assert result.pending_task is not None
    assert len(result.tasks_to_dispatch) == 1
    task = result.tasks_to_dispatch[0]
    assert task.status == TaskStatus.PENDING
    assert task.output_metadata["input_schema"] == [
        {"key": "name", "type": "string"}
    ]
    assert task.output_metadata["instruction"] == "from_prefill"  # ctx threading


@pytest.mark.asyncio
async def test_pending_template_emits_input_requested_with_pending_kind():
    handler = _StubPending(_Captured())
    result = await handler.handle(
        node=_make_node(), run=_make_run(), workflow=None,
        topology=None, completed_task=None, task_factory=_StubTaskFactory(),
    )
    events = [e for e in result.events if isinstance(e, InputRequested)]
    assert len(events) == 1
    assert events[0].pending_kind == "input"


@pytest.mark.asyncio
async def test_pending_template_writes_state_when_provisioned():
    captured = _Captured()
    provisioned = {"__file_ref": True, "file_id": "f-1", "artifact_id": "__root__"}
    handler = _StubPending(captured, provision_value=provisioned)

    result = await handler.handle(
        node=_make_node(), run=_make_run(), workflow=None,
        topology=None, completed_task=None, task_factory=_StubTaskFactory(),
    )
    assert result.state_updates["n1"] is provisioned
    # ``task_input`` is the provisioned ref, not the prefilled dict
    assert result.tasks_to_dispatch[0].resolved_input is provisioned


@pytest.mark.asyncio
async def test_pending_template_skips_state_when_no_provisioning():
    handler = _StubPending(_Captured(), provision_value=None)
    result = await handler.handle(
        node=_make_node(), run=_make_run(), workflow=None,
        topology=None, completed_task=None, task_factory=_StubTaskFactory(),
    )
    assert "n1" not in result.state_updates


@pytest.mark.asyncio
async def test_pending_template_provides_static_schema_provider_by_default():
    handler = _StubPending(_Captured())
    provider = handler.get_schema_provider()
    assert provider is not None
    # Default is StaticSchemaProvider — runtime task metadata wins.
    from app.layer2_application.node_handlers.schema_providers import (
        StaticSchemaProvider,
    )
    assert isinstance(provider, StaticSchemaProvider)
