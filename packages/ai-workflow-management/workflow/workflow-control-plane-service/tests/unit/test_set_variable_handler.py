"""Unit tests for SetVariableHandler.

Variables write into ``run.context.variables["__vars"]`` via state_updates.
Read-modify-write merges new keys with existing variables — preserves
previously-set names that this handler instance doesn't touch.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer2_application.node_handlers.set_variable_handler import SetVariableHandler
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer1_domain.value_objects import NodeType, RunStatus


def _make_resolver() -> VariableResolver:
    return VariableResolver(strategies=[MetadataResolverStrategy(), TraversalResolverStrategy()])


def _make_run(variables: dict[str, Any] | None = None) -> WorkflowRun:
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    if variables:
        run.context.variables.update(variables)
    return run


def _make_node(declarations: list[dict[str, Any]]) -> WorkflowNode:
    return WorkflowNode(
        id="set-vars-1", node_type=NodeType.SET_VARIABLE,
        config={"variables": declarations},
    )


@pytest.mark.asyncio
class TestSetVariableHandler:
    async def test_writes_literal(self):
        h = SetVariableHandler(_make_resolver())
        node = _make_node([{"name": "userCount", "value_expression": 42}])
        result = await h.handle(node, _make_run(), Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates["__vars"] == {"userCount": 42}

    async def test_writes_resolved_expression(self):
        # Set up upstream node output that the expression references.
        run = _make_run({"upstream_node": {"totalUsers": 100}})
        h = SetVariableHandler(_make_resolver())
        node = _make_node([
            {"name": "userCount", "value_expression": "{{$upstream_node.totalUsers}}"},
        ])
        result = await h.handle(node, run, Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates["__vars"] == {"userCount": 100}

    async def test_multiple_declarations(self):
        h = SetVariableHandler(_make_resolver())
        node = _make_node([
            {"name": "a", "value_expression": 1},
            {"name": "b", "value_expression": "two"},
        ])
        result = await h.handle(node, _make_run(), Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates["__vars"] == {"a": 1, "b": "two"}

    async def test_preserves_previously_set_variables(self):
        # An earlier SET_VARIABLE wrote ``userCount``; this one writes
        # ``label``. Both must survive.
        run = _make_run({"__vars": {"userCount": 100}})
        h = SetVariableHandler(_make_resolver())
        node = _make_node([{"name": "label", "value_expression": "active"}])
        result = await h.handle(node, run, Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates["__vars"] == {"userCount": 100, "label": "active"}

    async def test_overwrite_same_name(self):
        run = _make_run({"__vars": {"userCount": 100}})
        h = SetVariableHandler(_make_resolver())
        node = _make_node([{"name": "userCount", "value_expression": 50}])
        result = await h.handle(node, run, Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates["__vars"] == {"userCount": 50}

    async def test_immediate_completion_default_port(self):
        h = SetVariableHandler(_make_resolver())
        node = _make_node([])
        result = await h.handle(node, _make_run(), Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.completion_mode == "immediate"
        assert result.output_port == "out_default"

    async def test_metadata_records_names(self):
        h = SetVariableHandler(_make_resolver())
        node = _make_node([
            {"name": "a", "value_expression": 1},
            {"name": "b", "value_expression": 2},
        ])
        result = await h.handle(node, _make_run(), Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates[node.id]["metadata"]["type"] == "set_variable"
        assert result.state_updates[node.id]["metadata"]["names"] == ["a", "b"]

    async def test_invalid_declaration_skipped(self):
        h = SetVariableHandler(_make_resolver())
        node = _make_node([
            {"name": "ok", "value_expression": 1},
            "not-a-dict",
            {"name": "", "value_expression": "empty-name-skipped"},
            {"value_expression": "no-name-skipped"},
        ])
        result = await h.handle(node, _make_run(), Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates["__vars"] == {"ok": 1}


@pytest.mark.asyncio
class TestSetVariableHandlerCoercion:
    """Each declaration carries a ``type`` (and optional ``item_type``).
    The resolved value is coerced; on impossible coercion the raw value is
    stored as-is (pass-through), never raises.
    """

    async def test_coerces_string_to_number(self):
        h = SetVariableHandler(_make_resolver())
        node = _make_node([{"name": "n", "type": "number", "value_expression": "42"}])
        result = await h.handle(node, _make_run(), Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates["__vars"] == {"n": 42}

    async def test_coerces_string_to_boolean(self):
        h = SetVariableHandler(_make_resolver())
        node = _make_node([
            {"name": "a", "type": "boolean", "value_expression": "true"},
            {"name": "b", "type": "boolean", "value_expression": "no"},
        ])
        result = await h.handle(node, _make_run(), Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates["__vars"] == {"a": True, "b": False}

    async def test_coerces_anything_to_string(self):
        h = SetVariableHandler(_make_resolver())
        node = _make_node([
            {"name": "n", "type": "string", "value_expression": 42},
            {"name": "d", "type": "string", "value_expression": {"x": 1}},
        ])
        result = await h.handle(node, _make_run(), Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        # 42 → "42"; dict → JSON string.
        assert result.state_updates["__vars"]["n"] == "42"
        assert result.state_updates["__vars"]["d"] == '{"x": 1}'

    async def test_array_wraps_scalar(self):
        h = SetVariableHandler(_make_resolver())
        node = _make_node([
            {"name": "tags", "type": "array", "item_type": "string",
             "value_expression": "active"},
        ])
        result = await h.handle(node, _make_run(), Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates["__vars"] == {"tags": ["active"]}

    async def test_array_coerces_each_item(self):
        h = SetVariableHandler(_make_resolver())
        # Resolver passes through dict literals untouched, so feed a list directly.
        node = _make_node([
            {"name": "ns", "type": "array", "item_type": "number",
             "value_expression": ["1", "2", 3]},
        ])
        result = await h.handle(node, _make_run(), Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates["__vars"] == {"ns": [1, 2, 3]}

    async def test_array_parses_json_string(self):
        h = SetVariableHandler(_make_resolver())
        node = _make_node([
            {"name": "xs", "type": "array", "item_type": "number",
             "value_expression": "[1, 2, 3]"},
        ])
        result = await h.handle(node, _make_run(), Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates["__vars"] == {"xs": [1, 2, 3]}

    async def test_pass_through_on_impossible_coercion(self):
        # "abc" cannot be coerced to number — handler stores it as-is.
        h = SetVariableHandler(_make_resolver())
        node = _make_node([
            {"name": "n", "type": "number", "value_expression": "abc"},
        ])
        result = await h.handle(node, _make_run(), Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates["__vars"] == {"n": "abc"}

    async def test_object_parses_json_string(self):
        h = SetVariableHandler(_make_resolver())
        node = _make_node([
            {"name": "o", "type": "object", "value_expression": '{"k": 1}'},
        ])
        result = await h.handle(node, _make_run(), Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates["__vars"] == {"o": {"k": 1}}

    async def test_no_declared_type_is_unchanged(self):
        # Backwards compatibility: declarations without ``type`` work as before.
        h = SetVariableHandler(_make_resolver())
        node = _make_node([{"name": "x", "value_expression": "42"}])
        result = await h.handle(node, _make_run(), Workflow(id="wf-1", name="t", version="1"),
                                GraphTopology(), None, None)
        assert result.state_updates["__vars"] == {"x": "42"}
