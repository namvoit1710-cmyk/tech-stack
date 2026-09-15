"""SA-1751 — inline-node failure-semantics & routing hardening (executable contract).

Each test pins one finding from the SA-1751 audit AFTER the fix. Companion to
``test_node_handlers.py`` / ``test_merge_strategies.py`` / ``test_compute_handler.py``
(which pin the surrounding, unchanged behavior). Findings that turned out to be
deliberate, already-tested contracts (J1 COMPUTE soft-error, J4 SWITCH no-match)
are intentionally NOT changed here — see the module docstring of
``compute_handler`` and ``test_compute_handler.py::test_eval_error_is_soft``.

Covered:
  * J2  CONDITION fails loud (ExpressionError) on a resolution error instead of
        silently routing ``else``/False.
  * J3  CONDITION warns when the chosen branch has no wired edge (silent
        dead-end is now observable — symmetric with SWITCH).
  * J5  SWITCH legacy ``cases`` matching is type-aware (int 1 == float 1.0),
        not ``str()``-coerced.
  * J6  MERGE re-arms on a genuine loop back-edge re-entry (SA-846 cycle item)
        while still absorbing uneven-fan-in stragglers.
  * J8  APPROVAL_FLOW step sort tolerates missing/mixed ``stepOrder`` and routes
        the ``failure`` port on malformed data instead of crashing the node.
  * J9  MERGE honors ``required`` inputs (declared default or loud failure)
        instead of silently dropping the key.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.exceptions import ExpressionError, InputRequirementError
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.node_handlers.approval_flow_handler import (
    ApprovalFlowNodeHandler,
)
from app.layer2_application.node_handlers.condition_handler import ConditionNodeHandler
from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler
from app.layer2_application.node_handlers.switch_handler import SwitchNodeHandler
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    ConditionEvaluator,
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)


def _resolver():
    return VariableResolver([MetadataResolverStrategy(), TraversalResolverStrategy()])


def _factory():
    return TaskFactory()


def _topology(edge_map=None, adjacency=None, predecessors=None):
    return GraphTopology(
        edge_map=edge_map or {},
        adjacency=adjacency or {},
        predecessors=predecessors or {},
    )


# ═══════════════════════════ J2 — CONDITION fail-loud ════════════════════════


class TestConditionFailLoud:
    def _handler(self):
        return ConditionNodeHandler(ConditionEvaluator(_resolver()))

    async def test_expression_resolution_error_raises(self):
        node = WorkflowNode(
            id="cond", node_type=NodeType.CONDITION,
            config={"expression": "{{$missing.x}} > 5"},
        )
        workflow = Workflow(id="wf", nodes=[node])
        run = WorkflowRun(id="r", workflow_id="wf", context=RunContext())
        with pytest.raises(ExpressionError):
            await self._handler().handle(node, run, workflow, _topology(), None, _factory())

    async def test_structured_rule_resolution_error_raises(self):
        node = WorkflowNode(
            id="cond", node_type=NodeType.CONDITION,
            config={"data": [
                {"key": "{{$missing.x}}", "operations": ">", "value": 5},
            ]},
        )
        workflow = Workflow(id="wf", nodes=[node])
        run = WorkflowRun(id="r", workflow_id="wf", context=RunContext())
        with pytest.raises(ExpressionError):
            await self._handler().handle(node, run, workflow, _topology(), None, _factory())

    async def test_resolvable_condition_still_routes_normally(self):
        # Guard: fail-loud must NOT change the happy path.
        node = WorkflowNode(
            id="cond", node_type=NodeType.CONDITION,
            config={"expression": "{{$input.n}} > 5"},
        )
        workflow = Workflow(id="wf", nodes=[node])
        run = WorkflowRun(
            id="r", workflow_id="wf",
            context=RunContext(variables={"input": {"n": 7}}),
        )
        result = await self._handler().handle(node, run, workflow, _topology(), None, _factory())
        assert result.state_updates["cond"]["output"]["result"] is True


# ═══════════════════════════ J3 — CONDITION dead-end warning ═════════════════


class TestConditionDeadEndWarning:
    async def test_false_with_only_unlabeled_edge_warns(self, caplog):
        handler = ConditionNodeHandler(ConditionEvaluator(_resolver()))
        node = WorkflowNode(
            id="cond", node_type=NodeType.CONDITION,
            config={"expression": "{{$input.flag}}"},
        )
        tgt = WorkflowNode(id="t", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, tgt])
        # A single UNLABELED edge is treated as the "then" branch, so a False
        # result selects nothing → silent dead-end pre-fix.
        edge = WorkflowEdge(source_node_id="cond", target_node_id="t")
        dag = _topology(edge_map={"cond": [edge]}, adjacency={"cond": ["t"]})
        run = WorkflowRun(
            id="r", workflow_id="wf",
            context=RunContext(variables={"input": {"flag": False}}),
        )
        with caplog.at_level(logging.WARNING):
            result = await handler.handle(node, run, workflow, dag, None, _factory())
        assert result.taken_edge_ports == {}          # still a dead-end (behavior kept)
        assert "no outgoing edge accepted" in caplog.text   # ...but now observable

    async def test_matched_branch_does_not_warn(self, caplog):
        handler = ConditionNodeHandler(ConditionEvaluator(_resolver()))
        node = WorkflowNode(
            id="cond", node_type=NodeType.CONDITION,
            config={"expression": "{{$input.flag}}"},
        )
        tgt = WorkflowNode(id="t", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, tgt])
        edge = WorkflowEdge(source_node_id="cond", target_node_id="t", condition="then")
        dag = _topology(edge_map={"cond": [edge]}, adjacency={"cond": ["t"]})
        run = WorkflowRun(
            id="r", workflow_id="wf",
            context=RunContext(variables={"input": {"flag": True}}),
        )
        with caplog.at_level(logging.WARNING):
            result = await handler.handle(node, run, workflow, dag, None, _factory())
        assert edge.id in result.taken_edge_ports
        assert "no outgoing edge accepted" not in caplog.text


# ═══════════════════════════ J5 — SWITCH type-aware legacy match ═════════════


class TestSwitchLegacyTypeAwareMatch:
    async def test_int_value_matches_float_case(self):
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={"expression": "{{$input.n}}", "cases": [{"value": 1.0, "target": "t"}]},
        )
        tgt = WorkflowNode(id="t", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, tgt])
        edge = WorkflowEdge(source_node_id="sw", target_node_id="t")
        dag = _topology(edge_map={"sw": [edge]}, adjacency={"sw": ["t"]})
        run = WorkflowRun(
            id="r", workflow_id="wf",
            context=RunContext(variables={"input": {"n": 1}}),   # native int 1
        )
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        # Pre-fix: str(1) != str(1.0) → no match → dead-end. Post-fix: matches.
        assert edge.id in result.taken_edge_ports

    async def test_int_value_matches_in_values_list(self):
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={"expression": "{{$input.n}}", "cases": [{"values": [2.0, 3.0], "target": "t"}]},
        )
        tgt = WorkflowNode(id="t", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, tgt])
        edge = WorkflowEdge(source_node_id="sw", target_node_id="t")
        dag = _topology(edge_map={"sw": [edge]}, adjacency={"sw": ["t"]})
        run = WorkflowRun(
            id="r", workflow_id="wf",
            context=RunContext(variables={"input": {"n": 2}}),
        )
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        assert edge.id in result.taken_edge_ports


# ═══════════════════════════ J8 — APPROVAL_FLOW sort safety ══════════════════


class _FakeGraph:
    def to_dict(self):
        return {"nodes": [], "edges": []}


class _CapturingTransformer:
    def __init__(self):
        self.received = None

    def transform(self, steps):
        self.received = steps
        return _FakeGraph()


class _RaisingTransformer:
    def transform(self, steps):
        raise ValueError("boom")


class _FakeGovClient:
    def __init__(self, data):
        self._data = data

    async def get_approval_process(self, cr_id):
        return self._data


def _approval_node():
    return WorkflowNode(
        id="af", name="Approval", node_type=NodeType.APPROVAL_FLOW,
        config={"change_request_id": "CR-1"},
    )


def _approval_run():
    return WorkflowRun(id="r", workflow_id="wf", context=RunContext())


class TestApprovalFlowSortSafety:
    async def test_mixed_step_order_does_not_crash_and_sorts(self):
        steps = [
            {"stepId": "b", "stepOrder": 1},
            {"stepId": "a", "stepOrder": None},   # None mixed with int → old sort TypeError
            {"stepId": "c"},                       # missing stepOrder entirely
        ]
        transformer = _CapturingTransformer()
        handler = ApprovalFlowNodeHandler(_resolver(), _FakeGovClient(steps), transformer)
        node, workflow = _approval_node(), None
        workflow = Workflow(id="wf", nodes=[_approval_node()])
        result = await handler.handle(node, _approval_run(), workflow, _topology(), None, _factory())
        assert result.output_port == "success"
        # Numeric order first, then the order-less steps by stepId.
        assert [s["stepId"] for s in transformer.received] == ["b", "a", "c"]

    async def test_non_dict_steps_are_dropped(self):
        steps = [{"stepId": "a", "stepOrder": 1}, "not-a-dict", None]
        transformer = _CapturingTransformer()
        handler = ApprovalFlowNodeHandler(_resolver(), _FakeGovClient(steps), transformer)
        workflow = Workflow(id="wf", nodes=[_approval_node()])
        result = await handler.handle(_approval_node(), _approval_run(), workflow, _topology(), None, _factory())
        assert result.output_port == "success"
        assert [s["stepId"] for s in transformer.received] == ["a"]

    async def test_transform_error_routes_failure_port(self):
        steps = [{"stepId": "a", "stepOrder": 1}]
        handler = ApprovalFlowNodeHandler(_resolver(), _FakeGovClient(steps), _RaisingTransformer())
        workflow = Workflow(id="wf", nodes=[_approval_node()])
        result = await handler.handle(_approval_node(), _approval_run(), workflow, _topology(), None, _factory())
        assert result.output_port == "failure"
        assert "error" in result.state_updates["af"]["output"]


# ═══════════════════════════ J9 — MERGE honors required inputs ═══════════════


def _merge_node(config):
    return WorkflowNode(id="merge", node_type=NodeType.MERGE, config=config)


def _merge_run(topology, completed=()):
    ctx = RunContext(topology=topology, variables={"a": {"x": 1}})
    for n in completed:
        ctx.completed_nodes.add(n)
    return WorkflowRun(id="r", workflow_id="wf", status=RunStatus.RUNNING, context=ctx)


class TestMergeRequiredInputs:
    async def test_required_missing_no_default_raises(self):
        node = _merge_node({
            "merge_strategy": "any",
            "inputs": [
                {"source": "a", "key": "ka", "required": True},
                {"source": "b", "key": "kb", "required": True},
            ],
        })
        topology = SimpleNamespace(predecessors={"merge": ["a", "b"]}, adjacency={})
        run = _merge_run(topology, completed=["a"])   # 'b' never arrived; 'any' fires on 'a'
        with pytest.raises(InputRequirementError):
            await MergeNodeHandler().handle(
                node=node, run=run, workflow=None, topology=topology,
                completed_task=None, task_factory=None,
            )

    async def test_required_missing_uses_declared_default(self):
        node = _merge_node({
            "merge_strategy": "any",
            "inputs": [
                {"source": "a", "key": "ka", "required": True},
                {"source": "b", "key": "kb", "required": True},
            ],
            "defaults": {"kb": {"fallback": True}},
        })
        topology = SimpleNamespace(predecessors={"merge": ["a", "b"]}, adjacency={})
        run = _merge_run(topology, completed=["a"])
        result = await MergeNodeHandler().handle(
            node=node, run=run, workflow=None, topology=topology,
            completed_task=None, task_factory=None,
        )
        assert result.completion_mode == "immediate"
        assert result.state_updates["merge"]["output"]["kb"] == {"fallback": True}

    async def test_optional_missing_still_dropped_to_default(self):
        # required=False keeps the lenient behavior.
        node = _merge_node({
            "merge_strategy": "any",
            "inputs": [
                {"source": "a", "key": "ka", "required": True},
                {"source": "b", "key": "kb", "required": False},
            ],
        })
        topology = SimpleNamespace(predecessors={"merge": ["a", "b"]}, adjacency={})
        run = _merge_run(topology, completed=["a"])
        result = await MergeNodeHandler().handle(
            node=node, run=run, workflow=None, topology=topology,
            completed_task=None, task_factory=None,
        )
        assert result.completion_mode == "immediate"
        assert result.state_updates["merge"]["output"]["kb"] == {}


# ═══════════════════════════ J6 — MERGE cycle re-arm ═════════════════════════


class TestMergeCycleReArm:
    async def test_backedge_reentry_rearms_new_round(self):
        node = _merge_node({"merge_strategy": "any"})
        # merge -> d -> merge : 'd' is DOWNSTREAM of merge and a back-edge pred.
        topology = SimpleNamespace(
            predecessors={"merge": ["a", "d"]},
            adjacency={"merge": ["d"], "d": ["merge"], "a": ["merge"]},
        )
        ctx = RunContext(topology=topology, variables={})
        ctx.completed_nodes.add("a")
        run = WorkflowRun(id="r", workflow_id="wf", status=RunStatus.RUNNING, context=ctx)
        handler = MergeNodeHandler()

        first = await handler.handle(
            node=node, run=run, workflow=None, topology=topology,
            completed_task=Task(id="t-a", node_id="a"), task_factory=None,
        )
        assert first.completion_mode == "immediate"

        # Loop back-edge re-enters the fired merge → NEW round, must re-fire.
        ctx.completed_nodes.add("d")
        second = await handler.handle(
            node=node, run=run, workflow=None, topology=topology,
            completed_task=Task(id="t-d", node_id="d"), task_factory=None,
        )
        assert second.completion_mode == "immediate", (
            "a genuine loop back-edge must re-arm the merge, not be absorbed"
        )

    async def test_forward_straggler_still_absorbed(self):
        node = _merge_node({"merge_strategy": "any"})
        # No cycle: merge has no successors, so no predecessor is reachable
        # from it → every late arrival is a forward-fan-in straggler.
        topology = SimpleNamespace(
            predecessors={"merge": ["a", "b"]},
            adjacency={"merge": [], "a": ["merge"], "b": ["merge"]},
        )
        ctx = RunContext(topology=topology, variables={})
        ctx.completed_nodes.add("a")
        run = WorkflowRun(id="r", workflow_id="wf", status=RunStatus.RUNNING, context=ctx)
        handler = MergeNodeHandler()

        first = await handler.handle(
            node=node, run=run, workflow=None, topology=topology,
            completed_task=Task(id="t-a", node_id="a"), task_factory=None,
        )
        assert first.completion_mode == "immediate"

        ctx.completed_nodes.add("b")
        second = await handler.handle(
            node=node, run=run, workflow=None, topology=topology,
            completed_task=Task(id="t-b", node_id="b"), task_factory=None,
        )
        assert second.completion_mode == "absorbed"
