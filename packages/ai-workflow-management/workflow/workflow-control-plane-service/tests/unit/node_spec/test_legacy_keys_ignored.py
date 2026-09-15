"""Clean-break regression guard: legacy config keys are invisible.

Phase 3 of the NodeSpec redesign deleted every handler-side fallback
chain. These tests pin the new behavior: a node carrying ONLY legacy
keys behaves as unconfigured — the keys are not read, not mapped, not
fallback-resolved. Stored data was normalized to canonical keys by the
Phase 6 migration; anything still arriving on legacy keys is a bug in
the sender.
"""

from dataclasses import dataclass, field

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.node_handlers.merge_handler import (
    MergeNodeHandler,
)
from app.layer2_application.resolve.variable_resolver import (
    ConditionEvaluator,
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer2_application.node_handlers.condition_handler import (
    ConditionNodeHandler,
)


@dataclass
class _Topology:
    predecessors: dict = field(default_factory=dict)
    edge_map: dict = field(default_factory=dict)


def _run() -> WorkflowRun:
    return WorkflowRun(
        id="r1", workflow_id="wf",
        context=RunContext(variables={"input": {"n": 7}}),
    )


@pytest.mark.asyncio
async def test_condition_ignores_input_binding_data() -> None:
    """Structured rules live in config.data; input_binding.data is dead."""
    resolver = VariableResolver(
        [MetadataResolverStrategy(), TraversalResolverStrategy()],
    )
    handler = ConditionNodeHandler(ConditionEvaluator(resolver))
    node = WorkflowNode(
        id="cond", node_type=NodeType.CONDITION,
        # Legacy location — must NOT be evaluated. Without config the
        # node falls to the (empty) expression path → False.
        input_binding={"data": [
            {"key": "{{$input.n}}", "operations": ">", "value": 5},
        ]},
        config={},
    )
    workflow = Workflow(id="wf", nodes=[node])
    result = await handler.handle(
        node, _run(), workflow, _Topology(), None, None,
    )
    out = result.state_updates["cond"]["output"]
    # The structured path was NOT taken: the output trace shows the
    # (empty) legacy-expression evaluation, not the formatted rules
    # ("{{$input.n}} > 5") the input_binding payload would produce.
    assert out["expression"] == ""
    assert out["resolved"] == ""


@pytest.mark.asyncio
async def test_merge_ignores_wait_strategy_and_n_required() -> None:
    """Legacy keys + vocabulary are dead: handler sees spec defaults."""
    handler = MergeNodeHandler()
    node = WorkflowNode(
        id="m1", node_type=NodeType.MERGE,
        config={"wait_strategy": "wait_any", "n_required": 1},
    )
    workflow = Workflow(id="wf", nodes=[node])
    run = _run()
    topology = _Topology(predecessors={"m1": ["a", "b"]})
    # With legacy keys ignored the strategy defaults to "all"; only one
    # of two predecessors done → quorum NOT met (wait_any would fire).
    run.context.completed_nodes.add("a")
    result = await handler.handle(node, run, workflow, topology, None, None)
    assert result.completion_mode == "deferred"
