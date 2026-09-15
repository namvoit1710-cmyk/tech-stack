"""RunScopeManager.build_scope — the production scope-assembly path.

Stage 3b (no-``extracted`` OData model) made ``$variable`` input
contributions lazy: ``build_scope`` no longer eagerly flattens INPUT
outputs; instead the builder discovers the run's INPUT-node refs and
``UserVariablesFrame`` fetches their scalars on demand via the injected
``materialize`` callable. These tests pin that wiring without the full
engine/integration harness.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer2_application.orchestration.run_scope_manager import RunScopeManager
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.resolution_scope import ResolutionScopeBuilder


def _input_file_ref(file_id: str) -> dict:
    return {"__file_ref": True, "file_id": file_id, "extracted": {}}


def _top_level_run(*, variables=None, user_vars=None) -> WorkflowRun:
    # root_run_id == id → top-level (no parent / no inherited layer).
    return WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        root_run_id="run-1",
        context=RunContext(variables=dict(variables or {})),
        user_variables=dict(user_vars or {}),
    )


def _workflow_with_input(node_id="input_a", *, variables=None) -> Workflow:
    return Workflow(
        id="wf-1",
        name="wf",
        variables=dict(variables or {}),
        nodes=[WorkflowNode(id=node_id, name=node_id, node_type=NodeType.INPUT)],
        edges=[],
    )


async def test_build_scope_lazy_fetches_input_contribution():
    """A top-level run's INPUT scalar is fetched lazily and exposed as
    ``$variable.X`` — proving build_scope wires the materialize callable
    through the builder into UserVariablesFrame."""
    async def materialize(_ref):
        return {"username": "Alice", "_skip": "x", "nested": {"k": 1}}

    builder = ResolutionScopeBuilder(materialize=materialize)
    mgr = RunScopeManager(run_repo=None, scope_builder=builder)

    run = _top_level_run(variables={"input_a": _input_file_ref("f1")})
    workflow = _workflow_with_input("input_a", variables={"default_k": "d"})

    scope = mgr.build_scope(run, workflow)
    # default layer + lazily-fetched INPUT scalar; nested/underscore dropped.
    assert await scope.lookup("variable") == {"default_k": "d", "username": "Alice"}


async def test_build_scope_no_input_nodes_resolves_in_memory_only():
    builder = ResolutionScopeBuilder(materialize=None)
    mgr = RunScopeManager(run_repo=None, scope_builder=builder)

    run = _top_level_run(user_vars={"override_k": "o"})
    workflow = Workflow(id="wf-1", name="wf", variables={"default_k": "d"}, nodes=[], edges=[])

    scope = mgr.build_scope(run, workflow)
    assert await scope.lookup("variable") == {"default_k": "d", "override_k": "o"}
