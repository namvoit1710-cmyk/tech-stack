"""Tests for StandaloneNodeRunner — inline execution of ENGINE builtins."""

import pytest

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.condition_handler import ConditionNodeHandler
from app.layer2_application.node_handlers.switch_handler import SwitchNodeHandler
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.set_variable_handler import SetVariableHandler
from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler
from app.layer2_application.orchestration.standalone_node_runner import (
    NotTestableStandalone,
    StandaloneNodeRunner,
)
from app.layer2_application.resolve.variable_resolver import (
    ConditionEvaluator,
    TraversalResolverStrategy,
    VariableResolver,
)


def _runner():
    resolver = VariableResolver(strategies=[TraversalResolverStrategy()])
    reg = NodeHandlerRegistry()
    reg.register(NodeType.CONDITION, ConditionNodeHandler(ConditionEvaluator(resolver)))
    reg.register(NodeType.SWITCH, SwitchNodeHandler(resolver))
    reg.register(NodeType.COMPUTE, ComputeNodeHandler(resolver))
    reg.register(NodeType.SET_VARIABLE, SetVariableHandler(resolver))
    reg.register(NodeType.MERGE, MergeNodeHandler())
    return StandaloneNodeRunner(reg, resolver)


def _nd(base_type, **overrides):
    return NodeDefinition(id="nd-1", tenant_id="t1", name="N", base_type=base_type, **overrides)


@pytest.mark.asyncio
async def test_condition_true_branch():
    runner = _runner()
    nd = _nd(NodeType.CONDITION, locked_defaults={"expression": "{{$input.x}} > 5"})
    result = await runner.run(nd, {"x": 10})
    assert result.completion_payload["branch"] == "then"
    assert result.state_updates["nd-1"]["output"]["result"] is True


@pytest.mark.asyncio
async def test_condition_false_branch():
    runner = _runner()
    nd = _nd(NodeType.CONDITION, locked_defaults={"expression": "{{$input.x}} > 5"})
    result = await runner.run(nd, {"x": 1})
    assert result.completion_payload["branch"] == "else"
    assert result.state_updates["nd-1"]["output"]["result"] is False


@pytest.mark.asyncio
async def test_compute_value():
    runner = _runner()
    nd = _nd(NodeType.COMPUTE, locked_defaults={"expression": "{{$input.price}} * 2", "result_key": "total"})
    result = await runner.run(nd, {"price": 4})
    assert result.state_updates["nd-1"]["output"]["result"] == 8
    assert result.state_updates["total"] == 8


@pytest.mark.asyncio
async def test_set_variable():
    runner = _runner()
    nd = _nd(NodeType.SET_VARIABLE, locked_defaults={
        "variables": [{"name": "n", "type": "number", "value_expression": "{{$input.k}}"}],
    })
    result = await runner.run(nd, {"k": 7})
    assert result.state_updates["__vars"] == {"n": 7}


@pytest.mark.asyncio
async def test_merge_nothing_to_merge_completes():
    runner = _runner()
    nd = _nd(NodeType.MERGE, locked_defaults={"merge_strategy": "all"})
    result = await runner.run(nd, {})
    assert result.completion_mode == "immediate"
    assert result.state_updates["nd-1"]["output"] == {}


def test_is_inline_testable():
    runner = _runner()
    assert runner.is_inline_testable(NodeType.CONDITION)
    assert runner.is_inline_testable(NodeType.COMPUTE)
    assert not runner.is_inline_testable(NodeType.LOOP)
    assert not runner.is_inline_testable(NodeType.INPUT)
