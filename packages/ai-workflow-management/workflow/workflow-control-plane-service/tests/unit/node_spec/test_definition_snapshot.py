"""definition_snapshot (NodeSpec Phase 4) — snapshots out of config.

Pins the clean break at the reader sites: a `_node_definition` /
`_node_internals` dict smuggled into ``config`` is IGNORED; only the
``WorkflowNode.definition_snapshot`` field is read. Stored data was
re-homed by the Phase 6 migration.
"""

import pytest

from app.layer1_domain.entities.workflow import (
    Workflow,
    WorkflowNode,
    _node_to_snapshot,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.node_handlers.schema_providers import (
    StaticSchemaProvider,
)


class _Task:
    output_metadata: dict = {}


def test_entity_snapshot_round_trip() -> None:
    snapshot = {
        "input_schema": [{"key": "a"}],
        "locked_defaults": {"method": "GET"},
        "base_worker_id": "w1",
    }
    node = WorkflowNode(
        id="n1", workflow_id="n1", node_type=NodeType.TASK,
        definition_snapshot=snapshot,
    )
    as_dict = _node_to_snapshot(node)
    assert as_dict["definition_snapshot"] == snapshot

    restored = Workflow.from_snapshot_dict({
        "id": "wf", "name": "W", "nodes": [as_dict], "edges": [],
    })
    assert restored.nodes[0].definition_snapshot == snapshot


def test_definition_snapshot_not_in_parameters_round_trip() -> None:
    from app.layer1_domain.node_spec.parameter_mapper import merge_parameters

    node = WorkflowNode(
        id="n1", workflow_id="n1", node_type=NodeType.TASK,
        config={"input_types": {"a": "number"}},
        definition_snapshot={"input_schema": [{"key": "a"}]},
    )
    merged = merge_parameters(node.config, node.input_binding)
    assert "definition_snapshot" not in merged
    assert "_node_definition" not in merged


@pytest.mark.asyncio
async def test_schema_provider_ignores_config_smuggled_snapshot() -> None:
    provider = StaticSchemaProvider()
    node = WorkflowNode(
        id="n1", workflow_id="n1", node_type=NodeType.INPUT,
        # Legacy location — dead. Only definition_snapshot is read.
        config={"_node_definition": {"input_schema": [{"key": "legacy"}]}},
    )
    schema, version, source = await provider.resolve(
        node=node, task=_Task(), run=None, view=None,
    )
    assert schema == []
    assert version == "v0"
    assert source == "design_time"


@pytest.mark.asyncio
async def test_schema_provider_reads_snapshot_field() -> None:
    provider = StaticSchemaProvider()
    node = WorkflowNode(
        id="n1", workflow_id="n1", node_type=NodeType.INPUT,
        definition_snapshot={"input_schema": [{"key": "real"}]},
    )
    schema, _, source = await provider.resolve(
        node=node, task=_Task(), run=None, view=None,
    )
    assert schema == [{"key": "real"}]
    assert source == "design_time"


@pytest.mark.asyncio
async def test_task_handler_ignores_config_smuggled_internals() -> None:
    from app.layer2_application.node_handlers.task_handler import (
        TaskNodeHandler,
    )
    from app.layer2_application.resolve.variable_resolver import (
        TraversalResolverStrategy,
        VariableResolver,
    )
    from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun

    handler = TaskNodeHandler(
        variable_resolver=VariableResolver([TraversalResolverStrategy()]),
    )
    node = WorkflowNode(
        id="n1", workflow_id="n1", node_type=NodeType.TASK, worker_type="w1",
        input_binding={"method": "DELETE"},
        config={"_node_internals": {"locked_defaults": {"method": "GET"}}},
    )
    run = WorkflowRun(id="r1", workflow_id="wf", context=RunContext())
    resolved = await handler._apply_node_definition(node, {"method": "DELETE"}, run)
    # Legacy locked_defaults NOT applied — DELETE survives.
    assert resolved == {"method": "DELETE"}
