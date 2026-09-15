"""Unit tests for all node handlers in layer2_application/node_handlers/."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    VariableResolver,
    ConditionEvaluator,
    TraversalResolverStrategy,
    MetadataResolverStrategy,
)
from app.layer2_application.node_handlers.base import (
    NodeHandlerResult,
    NodeHandlerRegistry,
)
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.node_handlers.task_handler import TaskNodeHandler
from app.layer2_application.node_handlers.condition_handler import ConditionNodeHandler
from app.layer2_application.node_handlers.switch_handler import SwitchNodeHandler
from app.layer2_application.node_handlers.loop_handler import LoopNodeHandler
from app.layer2_application.node_handlers.parallel_handler import ParallelNodeHandler
from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler
from app.layer2_application.node_handlers.workflow_handler import WorkflowNodeHandler
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler, _safe_eval, ComputeExpressionError
from app.layer2_application.node_handlers.approval_flow_handler import ApprovalFlowNodeHandler
from app.layer1_domain.events import NodeApprovalFlow
from app.layer1_domain.exceptions import ExpressionError, InputRequirementError, MaxDepthExceededError, UnsupportedNodeTypeError


def _resolver():
    return VariableResolver([MetadataResolverStrategy(), TraversalResolverStrategy()])


def _factory():
    return TaskFactory()


def _topology(edge_map=None, adjacency=None, predecessors=None, entry=None, leaf=None):
    return GraphTopology(
        edge_map=edge_map or {},
        adjacency=adjacency or {},
        predecessors=predecessors or {},
        entry_nodes=entry or [],
        leaf_nodes=leaf or [],
    )


# ────────────── NodeHandlerRegistry ──────────────


class TestNodeHandlerRegistry:
    def test_register_and_get(self):
        registry = NodeHandlerRegistry()
        handler = MagicMock()
        registry.register(NodeType.TRIGGER, handler)
        assert registry.get(NodeType.TRIGGER) is handler

    def test_get_unregistered_raises(self):
        registry = NodeHandlerRegistry()
        with pytest.raises(UnsupportedNodeTypeError):
            registry.get(NodeType.TRIGGER)


class TestNodeHandlerResult:
    def test_merge_basic(self):
        a = NodeHandlerResult(tasks_to_dispatch=[Task(id="t1")], events=[])
        b = NodeHandlerResult(
            tasks_to_dispatch=[Task(id="t2")],
            skipped_node_ids=["s1"],
            state_updates={"k": "v"},
            loop_state_updates={"l": "x"},
            run_status_change=RunStatus.FAILED,
            events=[],
        )
        a.merge(b)
        assert len(a.tasks_to_dispatch) == 2
        assert a.skipped_node_ids == ["s1"]
        assert a.state_updates == {"k": "v"}
        assert a.loop_state_updates == {"l": "x"}
        assert a.run_status_change == RunStatus.FAILED

    def test_merge_loop_state_none_to_dict(self):
        a = NodeHandlerResult(loop_state_updates=None, events=[])
        b = NodeHandlerResult(loop_state_updates={"l": 1}, events=[])
        a.merge(b)
        assert a.loop_state_updates == {"l": 1}

    def test_merge_run_status_none_preserved(self):
        a = NodeHandlerResult(run_status_change=RunStatus.COMPLETED, events=[])
        b = NodeHandlerResult(run_status_change=None, events=[])
        a.merge(b)
        assert a.run_status_change == RunStatus.COMPLETED


# ────────────── TriggerHandler ──────────────


class TestTriggerHandler:
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_basic_trigger(self):
        handler = TriggerNodeHandler()
        node = WorkflowNode(id="trig", node_type=NodeType.TRIGGER, config={"trigger_type": "api"})
        target = WorkflowNode(id="task1", node_type=NodeType.TASK, worker_type="w1")
        workflow = Workflow(id="wf", nodes=[node, target])
        edge = WorkflowEdge(source_node_id="trig", target_node_id="task1")
        dag = _topology(edge_map={"trig": [edge]})
        run = WorkflowRun(id="r1", workflow_id="wf", input_data={"key": "val"})

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert "trig" in result.state_updates
        assert "trigger" in result.state_updates
        assert result.state_updates["trigger"]["body"] == {"key": "val"}
        assert len(result.tasks_to_dispatch) == 1
        assert result.tasks_to_dispatch[0].node_id == "task1"

    @pytest.mark.asyncio
    async def test_trigger_default_type(self):
        handler = TriggerNodeHandler()
        node = WorkflowNode(id="trig", node_type=NodeType.TRIGGER, config={})
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology(edge_map={"trig": []})
        run = WorkflowRun(id="r1", workflow_id="wf")

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert result.state_updates["trigger"]["type"] == "manual"

    @pytest.mark.asyncio
    async def test_trigger_missing_target(self):
        handler = TriggerNodeHandler()
        node = WorkflowNode(id="trig", node_type=NodeType.TRIGGER, config={})
        edge = WorkflowEdge(source_node_id="trig", target_node_id="ghost")
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology(edge_map={"trig": [edge]})
        run = WorkflowRun(id="r1", workflow_id="wf")

        result = await handler.handle(node, run, workflow, dag, None, _factory())
        assert len(result.tasks_to_dispatch) == 0


# ────────────── TaskHandler ──────────────


class TestTaskHandler:
    @pytest.mark.asyncio
    async def test_basic_task_dispatch(self):
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            input_binding={"key": "{{$input.data}}"},
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"data": "hello"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert len(result.tasks_to_dispatch) == 1
        task = result.tasks_to_dispatch[0]
        assert task.resolved_input == {"key": "hello"}
        assert task.started_at is not None
        # NodeDispatched event is published by OrchestrationEngine, not the handler
        assert len(result.events) == 0

    @pytest.mark.asyncio
    async def test_no_worker_id_raises(self):
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(id="task1", node_type=NodeType.TASK, worker_type=None)
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf")

        with pytest.raises(ExpressionError, match="no worker_type"):
            await handler.handle(node, run, workflow, dag, None, _factory())

    @pytest.mark.asyncio
    async def test_resolution_error_raises(self):
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            input_binding={"key": "{{$missing.data}}"},
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf", context=RunContext())

        with pytest.raises(ExpressionError, match="input resolution failed"):
            await handler.handle(node, run, workflow, dag, None, _factory())

    @pytest.mark.asyncio
    async def test_no_input_binding(self):
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(id="task1", node_type=NodeType.TASK, worker_type="w1")
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf")

        result = await handler.handle(node, run, workflow, dag, None, _factory())
        assert result.tasks_to_dispatch[0].resolved_input == {}


class _StubGovernanceClient:
    def __init__(self, payload):
        self._payload = payload

    async def get_approval_process(self, change_request_id: str):
        return self._payload


class TestApprovalFlowHandler:

    @pytest.mark.asyncio
    async def test_accepts_bare_list_response_from_real_governance_client(self):
        # Regression: the real HttpGovernanceClient.get_approval_process returns a
        # BARE list of steps (not a {"processes": [...]} dict). The handler did
        # approval_data.get("processes") and raised AttributeError on the list.
        # It must accept the list shape and build the graph.
        steps = [
            {"stepId": "1", "stepOrder": 1, "processType": "APPROVE", "status": "PENDING",
             "assignToId": "u1", "assignToName": "Approver 1", "nextSteps": "2"},
            {"stepId": "2", "stepOrder": 2, "processType": "CONTRIBUTE", "status": "INACTIVE",
             "assignToId": "u2", "assignToName": "Contributor 2", "nextSteps": ""},
        ]
        handler = ApprovalFlowNodeHandler(
            variable_resolver=_resolver(),
            governance_client=_StubGovernanceClient(steps),
        )
        node = WorkflowNode(
            id="approval-1",
            node_type=NodeType.APPROVAL_FLOW,
            config={"change_request_id": "CR-100"},
        )
        workflow = Workflow(id="wf", nodes=[node], edges=[])
        dag = _topology(edge_map={"approval-1": []})
        run = WorkflowRun(id="r1", workflow_id="wf", context=RunContext())

        # Dispatch by keyword exactly as the engine does.
        result = await handler.handle(
            node=node, run=run, workflow=workflow, topology=dag,
            completed_task=None, task_factory=_factory(),
        )

        assert result.output_port == "success"
        output = result.state_updates["approval-1"]["output"]
        assert output["change_request_id"] == "CR-100"
        assert len(output["nodes"]) >= 2  # graph built from the list, no crash

    @pytest.mark.asyncio
    async def test_empty_list_response_does_not_crash(self):
        # The client returns [] on failure / not-found; the handler must not crash
        # (a non-list previously also broke len(approval_steps)).
        handler = ApprovalFlowNodeHandler(
            variable_resolver=_resolver(),
            governance_client=_StubGovernanceClient([]),
        )
        node = WorkflowNode(
            id="approval-1",
            node_type=NodeType.APPROVAL_FLOW,
            config={"change_request_id": "CR-404"},
        )
        workflow = Workflow(id="wf", nodes=[node], edges=[])
        dag = _topology(edge_map={"approval-1": []})
        run = WorkflowRun(id="r1", workflow_id="wf", context=RunContext())
        result = await handler.handle(
            node=node, run=run, workflow=workflow, topology=dag,
            completed_task=None, task_factory=_factory(),
        )
        assert result.state_updates["approval-1"]["metadata"]["step_count"] == 0

    @pytest.mark.asyncio
    async def test_success_builds_graph_and_emits_event(self):
        handler = ApprovalFlowNodeHandler(
            variable_resolver=_resolver(),
            governance_client=_StubGovernanceClient({
                "processes": [
                    {
                        "stepId": "1",
                        "stepOrder": 1,
                        "processType": "APPROVE",
                        "status": "PENDING",
                        "assignToId": "u1",
                        "assignToName": "Approver 1",
                        "nextSteps": "2",
                    },
                    {
                        "stepId": "2",
                        "stepOrder": 2,
                        "processType": "CONTRIBUTE",
                        "status": "INACTIVE",
                        "assignToId": "u2",
                        "assignToName": "Contributor 2",
                        "nextSteps": "",
                    },
                ]
            }),
        )
        node = WorkflowNode(
            id="approval-1",
            name="Fetch Approval",
            node_type=NodeType.APPROVAL_FLOW,
            config={
                "change_request_id": "CR-100",
                "instruction": "Fetch and visualize the approval flow for the CR.",
            },
        )
        success_node = WorkflowNode(id="next-success", node_type=NodeType.TASK, worker_type="worker")
        failure_node = WorkflowNode(id="next-failure", node_type=NodeType.TASK, worker_type="worker")
        workflow = Workflow(
            id="wf",
            nodes=[node, success_node, failure_node],
            edges=[
                WorkflowEdge(id="e1", source_node_id="approval-1", target_node_id="next-success", source_port_id="success"),
                WorkflowEdge(id="e2", source_node_id="approval-1", target_node_id="next-failure", source_port_id="failure"),
            ],
        )
        dag = _topology(edge_map={"approval-1": workflow.edges})
        run = WorkflowRun(id="r1", workflow_id="wf", context=RunContext())

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert result.state_updates["approval-1"]["output"]["change_request_id"] == "CR-100"
        assert len(result.state_updates["approval-1"]["output"]["nodes"]) == 4
        # The handler selects the output port; fan-out to the matching edge
        # (next-success) is compute_fanout's job, not the handler's.
        assert result.output_port == "success"
        event = next(e for e in result.events if isinstance(e, NodeApprovalFlow))
        assert event.output_port == "success"
        assert event.change_request_id == "CR-100"
        # node_name + instruction ride the node.approval_flow.result event payload.
        assert event.node_name == "Fetch Approval"
        assert event.instruction == "Fetch and visualize the approval flow for the CR."

    @pytest.mark.asyncio
    async def test_failure_routes_to_failure_port(self):
        handler = ApprovalFlowNodeHandler(variable_resolver=_resolver(), governance_client=None)
        node = WorkflowNode(
            id="approval-1",
            node_type=NodeType.APPROVAL_FLOW,
            config={"change_request_id": "CR-404"},
        )
        success_node = WorkflowNode(id="next-success", node_type=NodeType.TASK, worker_type="worker")
        failure_node = WorkflowNode(id="next-failure", node_type=NodeType.TASK, worker_type="worker")
        workflow = Workflow(
            id="wf",
            nodes=[node, success_node, failure_node],
            edges=[
                WorkflowEdge(id="e1", source_node_id="approval-1", target_node_id="next-success", source_port_id="success"),
                WorkflowEdge(id="e2", source_node_id="approval-1", target_node_id="next-failure", source_port_id="failure"),
            ],
        )
        dag = _topology(edge_map={"approval-1": workflow.edges})
        run = WorkflowRun(id="r1", workflow_id="wf", context=RunContext())

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert result.state_updates["approval-1"]["output"]["error"] == "Governance API client not configured"
        # Handler routes via the failure port; dispatching next-failure is
        # compute_fanout's responsibility (port-override fan-out), not the handler's.
        assert result.output_port == "failure"
        event = next(e for e in result.events if isinstance(e, NodeApprovalFlow))
        assert event.output_port == "failure"

    @pytest.mark.asyncio
    async def test_input_require_present_passes(self):
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            input_binding={"url": "{{$input.url}}", "method": "{{$input.method}}"},
            input_require=["url", "method"],
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"url": "http://x", "method": "GET"}}),
        )
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        assert len(result.tasks_to_dispatch) == 1

    @pytest.mark.asyncio
    async def test_input_require_missing_raises(self):
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            input_binding={"url": "{{$input.url}}"},
            input_require=["url", "method"],
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"url": "http://x"}}),
        )
        with pytest.raises(InputRequirementError, match="missing required input keys.*method"):
            await handler.handle(node, run, workflow, dag, None, _factory())

    @pytest.mark.asyncio
    async def test_input_require_none_value_raises(self):
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            input_binding={"url": "{{$input.url}}"},
            input_require=["url"],
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"url": None}}),
        )
        with pytest.raises(InputRequirementError, match="missing required input keys.*url"):
            await handler.handle(node, run, workflow, dag, None, _factory())

    @pytest.mark.asyncio
    async def test_input_require_empty_list_no_validation(self):
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            input_require=[],
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf")
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        assert len(result.tasks_to_dispatch) == 1

    def test_input_require_string_coercion(self):
        node = WorkflowNode(id="task1", node_type=NodeType.TASK, input_require="url, method")
        assert node.input_require == ["url", "method"]

    @pytest.mark.asyncio
    async def test_input_types_coercion(self):
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            input_binding={"count": "{{$input.count}}", "enabled": "{{$input.enabled}}"},
            config={"input_types": {"count": "number", "enabled": "boolean"}},
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"count": "5", "enabled": "true"}}),
        )
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        task = result.tasks_to_dispatch[0]
        assert task.resolved_input["count"] == 5
        assert task.resolved_input["enabled"] is True

    # ────────── SA-517: Instruction and Agent Tests ──────────

    @pytest.mark.asyncio
    async def test_instruction_resolved_by_variable_resolver(self):
        """SA-517: instruction field should be resolved using VariableResolver."""
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            instruction="Summarize the document at {{$input.doc_url}}",
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"doc_url": "https://example.com/doc.pdf"}}),
        )
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        task = result.tasks_to_dispatch[0]
        assert task.resolved_input.get("_instruction") == "Summarize the document at https://example.com/doc.pdf"

    @pytest.mark.asyncio
    async def test_instruction_empty_not_included(self):
        """SA-517: Empty instruction should not add _instruction to resolved_input."""
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            instruction="",
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf")
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        task = result.tasks_to_dispatch[0]
        assert "_instruction" not in task.resolved_input

    @pytest.mark.asyncio
    async def test_instruction_plain_string_passed_through(self):
        """SA-517: Plain instruction without variables passes through unchanged."""
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            instruction="Classify this email as spam or ham.",
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf")
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        task = result.tasks_to_dispatch[0]
        assert task.resolved_input.get("_instruction") == "Classify this email as spam or ham."

    @pytest.mark.asyncio
    async def test_action_included_in_resolved_input(self):
        """SA-517: action field should be included as _action in resolved_input."""
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            action="summarize_document",
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf")
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        task = result.tasks_to_dispatch[0]
        assert task.resolved_input.get("_action") == "summarize_document"

    @pytest.mark.asyncio
    async def test_action_empty_not_included(self):
        """SA-517: Empty action should not add _action to resolved_input."""
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            action="",
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf")
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        task = result.tasks_to_dispatch[0]
        assert "_action" not in task.resolved_input

    @pytest.mark.asyncio
    async def test_agents_included_in_resolved_input(self):
        """SA-517: agents list should be included as _agents in resolved_input."""
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            agents=[{"agent_id": "agent-summarizer", "priority": 10}],
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf")
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        task = result.tasks_to_dispatch[0]
        assert task.resolved_input.get("_agents") == [
            {"agent_id": "agent-summarizer", "role": "", "priority": 10}
        ]

    @pytest.mark.asyncio
    async def test_empty_agents_not_included(self):
        """SA-517: Empty agents list should not add _agents to resolved_input."""
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            agents=[],
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf")
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        task = result.tasks_to_dispatch[0]
        assert "_agents" not in task.resolved_input

    @pytest.mark.asyncio
    async def test_node_agents_independent_from_workflow_agents(self):
        """SA-517: Node agents should NOT inherit from workflow agents."""
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="task1", node_type=NodeType.TASK, worker_type="w1",
            agents=[{"agent_id": "node-specific-agent"}],
        )
        workflow = Workflow(
            id="wf", nodes=[node],
            agents=[{"agent_id": "workflow-level-agent"}],
        )
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf")
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        task = result.tasks_to_dispatch[0]
        
        # Only node-level agents should be included, not workflow agents
        agents = task.resolved_input.get("_agents", [])
        agent_ids = [a["agent_id"] for a in agents]
        assert "node-specific-agent" in agent_ids
        assert "workflow-level-agent" not in agent_ids

    @pytest.mark.asyncio
    async def test_full_agent_support_fields(self):
        """SA-517: Full example with all agent support fields."""
        handler = TaskNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="summarize-thread",
            name="Summarize Thread",
            node_type=NodeType.TASK,
            worker_type="agent-worker",
            action="summarize_document",
            instruction="Summarize the ticket at {{$input.ticket_id}}. Keep it under 3 sentences.",
            agents=[
                {"agent_id": "agent-summarizer-gpt4o", "role": "primary", "priority": 10},
            ],
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"ticket_id": "TICKET-123"}}),
        )
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        task = result.tasks_to_dispatch[0]
        
        assert task.resolved_input.get("_action") == "summarize_document"
        assert task.resolved_input.get("_instruction") == "Summarize the ticket at TICKET-123. Keep it under 3 sentences."
        assert task.resolved_input.get("_agents") == [
            {"agent_id": "agent-summarizer-gpt4o", "role": "primary", "priority": 10}
        ]


# ────────────── ConditionHandler ──────────────


class TestConditionHandler:
    def _handler(self):
        resolver = _resolver()
        evaluator = ConditionEvaluator(resolver)
        return ConditionNodeHandler(evaluator)

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_condition_true_takes_then(self):
        handler = self._handler()
        node = WorkflowNode(id="cond", node_type=NodeType.CONDITION, config={"expression": "{{$input.flag}}"})
        then_node = WorkflowNode(id="then", node_type=NodeType.TASK, worker_type="w")
        else_node = WorkflowNode(id="else", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, then_node, else_node])
        edges = [
            WorkflowEdge(source_node_id="cond", target_node_id="then", condition="then"),
            WorkflowEdge(source_node_id="cond", target_node_id="else", condition="else"),
        ]
        dag = _topology(
            edge_map={"cond": edges},
            adjacency={"cond": ["then", "else"], "then": [], "else": []},
            predecessors={"cond": [], "then": ["cond"], "else": ["cond"]},
        )
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"flag": True}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "then" in dispatched_ids
        assert "else" not in dispatched_ids
        assert "else" in result.skipped_node_ids
        # Phase 4: the branch trace is folded into completion_payload
        # (merged onto NodeCompleted by the engine), not a separate event.
        assert result.completion_payload["branch"] == "then"
        assert result.completion_payload["condition_result"] is True
        # Edge emission moved to the engine; handler now records taken ports
        assert len(result.taken_edge_ports) == 1

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_condition_false_takes_else(self):
        handler = self._handler()
        node = WorkflowNode(id="cond", node_type=NodeType.CONDITION, config={"expression": "{{$input.flag}}"})
        then_node = WorkflowNode(id="then", node_type=NodeType.TASK, worker_type="w")
        else_node = WorkflowNode(id="else", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, then_node, else_node])
        edges = [
            WorkflowEdge(source_node_id="cond", target_node_id="then", condition="then"),
            WorkflowEdge(source_node_id="cond", target_node_id="else", condition="else"),
        ]
        dag = _topology(
            edge_map={"cond": edges},
            adjacency={"cond": ["then", "else"], "then": [], "else": []},
            predecessors={"cond": [], "then": ["cond"], "else": ["cond"]},
        )
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"flag": False}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "else" in dispatched_ids
        assert "then" not in dispatched_ids

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_condition_resolution_error_defaults_false(self):
        handler = self._handler()
        node = WorkflowNode(id="cond", node_type=NodeType.CONDITION, config={"expression": "{{$missing.x}}"})
        then_node = WorkflowNode(id="then", node_type=NodeType.TASK, worker_type="w")
        else_node = WorkflowNode(id="else", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, then_node, else_node])
        edges = [
            WorkflowEdge(source_node_id="cond", target_node_id="then", condition="then"),
            WorkflowEdge(source_node_id="cond", target_node_id="else", condition="else"),
        ]
        dag = _topology(
            edge_map={"cond": edges},
            adjacency={"cond": ["then", "else"], "then": [], "else": []},
            predecessors={"cond": [], "then": ["cond"], "else": ["cond"]},
        )
        run = WorkflowRun(id="r1", workflow_id="wf", context=RunContext())

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "else" in dispatched_ids

    @pytest.mark.asyncio
    async def test_condition_no_longer_emits_downstream_skip(self):
        """Edge-based dispatch: the CONDITION handler no longer pre-computes
        a downstream skip list. Only ``taken_edge_ports`` is populated; the
        not-taken branch (and its downstream subtree) simply never receive
        a fanout action and therefore never get tasks."""
        handler = self._handler()
        node = WorkflowNode(id="cond", node_type=NodeType.CONDITION, config={"expression": "{{$input.flag}}"})
        then_node = WorkflowNode(id="then", node_type=NodeType.TASK, worker_type="w")
        else_node = WorkflowNode(id="else", node_type=NodeType.TASK, worker_type="w")
        else_child = WorkflowNode(id="else_child", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, then_node, else_node, else_child])
        then_edge = WorkflowEdge(source_node_id="cond", target_node_id="then", condition="then")
        else_edge = WorkflowEdge(source_node_id="cond", target_node_id="else", condition="else")
        edges = [then_edge, else_edge]
        dag = _topology(
            edge_map={"cond": edges},
            adjacency={"cond": ["then", "else"], "then": [], "else": ["else_child"], "else_child": []},
            predecessors={"cond": [], "then": ["cond"], "else": ["cond"], "else_child": ["else"]},
        )
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"flag": True}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        # No downstream skip list.
        assert result.skipped_node_ids == []
        # Only the "then" edge is taken; "else" is silently dropped.
        assert then_edge.id in result.taken_edge_ports
        assert else_edge.id not in result.taken_edge_ports

    @pytest.mark.asyncio
    async def test_condition_output_shows_resolved_expression_legacy(self):
        """The node output carries the evaluation trace — raw expression,
        resolved expression, result, branch — for the output panel."""
        handler = self._handler()
        node = WorkflowNode(
            id="cond", node_type=NodeType.CONDITION,
            config={"expression": "{{$input.n}} > 5"},
        )
        workflow = Workflow(id="wf", nodes=[node])
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"n": 7}}),
        )

        result = await handler.handle(node, run, workflow, _topology(), None, _factory())

        out = result.state_updates["cond"]["output"]
        assert out["result"] is True
        assert out["branch"] == "then"
        assert out["expression"] == "{{$input.n}} > 5"
        assert out["resolved"] == "7 > 5"

    @pytest.mark.asyncio
    async def test_condition_output_shows_resolved_rules_structured(self):
        handler = self._handler()
        node = WorkflowNode(
            id="cond", node_type=NodeType.CONDITION,
            config={
                "data": [
                    {"key": "{{$input.n}}", "operations": ">", "value": 5},
                ],
            },
        )
        workflow = Workflow(id="wf", nodes=[node])
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"n": 7}}),
        )

        result = await handler.handle(node, run, workflow, _topology(), None, _factory())

        out = result.state_updates["cond"]["output"]
        assert out["result"] is True
        assert out["branch"] == "then"
        assert out["expression"] == "{{$input.n}} > 5"
        assert out["resolved"] == "7 > 5"


# ────────────── SwitchHandler ──────────────


class TestSwitchHandler:
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_switch_case_match(self):
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={
                "expression": "{{$input.color}}",
                "cases": [
                    {"value": "red", "target": "redNode"},
                    {"value": "blue", "target": "blueNode"},
                ],
                "default": "defaultNode",
            },
        )
        red_node = WorkflowNode(id="redNode", node_type=NodeType.TASK, worker_type="w")
        blue_node = WorkflowNode(id="blueNode", node_type=NodeType.TASK, worker_type="w")
        default_node = WorkflowNode(id="defaultNode", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, red_node, blue_node, default_node])
        edges = [
            WorkflowEdge(source_node_id="sw", target_node_id="redNode"),
            WorkflowEdge(source_node_id="sw", target_node_id="blueNode"),
            WorkflowEdge(source_node_id="sw", target_node_id="defaultNode", condition="default"),
        ]
        dag = _topology(
            edge_map={"sw": edges},
            adjacency={"sw": ["redNode", "blueNode", "defaultNode"]},
        )
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"color": "red"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "redNode" in dispatched_ids
        assert len(result.skipped_node_ids) > 0

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_switch_default_fallback(self):
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={
                "expression": "{{$input.color}}",
                "cases": [{"value": "red", "target": "redNode"}],
                "default": "defaultNode",
            },
        )
        red_node = WorkflowNode(id="redNode", node_type=NodeType.TASK, worker_type="w")
        default_node = WorkflowNode(id="defaultNode", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, red_node, default_node])
        edges = [
            WorkflowEdge(source_node_id="sw", target_node_id="redNode"),
            WorkflowEdge(source_node_id="sw", target_node_id="defaultNode", condition="default"),
        ]
        dag = _topology(
            edge_map={"sw": edges},
            adjacency={"sw": ["redNode", "defaultNode"]},
        )
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"color": "green"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "defaultNode" in dispatched_ids

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_switch_resolution_error(self):
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={
                "expression": "{{$missing.x}}",
                "cases": [],
                "default": "defaultNode",
            },
        )
        default_node = WorkflowNode(id="defaultNode", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, default_node])
        edges = [WorkflowEdge(source_node_id="sw", target_node_id="defaultNode", condition="default")]
        dag = _topology(edge_map={"sw": edges}, adjacency={"sw": ["defaultNode"]})
        run = WorkflowRun(id="r1", workflow_id="wf", context=RunContext())

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "defaultNode" in dispatched_ids

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_switch_case_values_list(self):
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={
                "expression": "{{$input.x}}",
                "cases": [{"values": ["a", "b"], "target": "matchNode"}],
            },
        )
        match_node = WorkflowNode(id="matchNode", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, match_node])
        edges = [WorkflowEdge(source_node_id="sw", target_node_id="matchNode")]
        dag = _topology(edge_map={"sw": edges}, adjacency={"sw": ["matchNode"]})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"x": "b"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "matchNode" in dispatched_ids

    @pytest.mark.asyncio
    async def test_switch_no_match_no_default(self):
        """Edge-based dispatch: when no case matches and there is no default,
        the handler simply emits no taken edges and no skip list. The
        unmatched target receives no fanout action."""
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={
                "expression": "{{$input.x}}",
                "cases": [{"value": "z", "target": "zNode"}],
            },
        )
        z_node = WorkflowNode(id="zNode", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, z_node])
        edges = [WorkflowEdge(source_node_id="sw", target_node_id="zNode")]
        dag = _topology(edge_map={"sw": edges}, adjacency={"sw": ["zNode"]})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"x": "nope"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert result.skipped_node_ids == []
        assert result.taken_edge_ports == {}

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_switch_edge_condition_case_match(self):
        """Line 62-63: edge.condition == 'case:value' matches resolved value."""
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={
                "expression": "{{$input.x}}",
                "cases": [],  # no case config
            },
        )
        target = WorkflowNode(id="t1", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, target])
        edges = [WorkflowEdge(source_node_id="sw", target_node_id="t1", condition="case:red")]
        dag = _topology(edge_map={"sw": edges}, adjacency={"sw": ["t1"]})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"x": "red"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "t1" in dispatched_ids

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_switch_edge_default_condition_when_no_match(self):
        """Line 64-65 + 68-70: default edge taken when no case matches."""
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={
                "expression": "{{$input.x}}",
                "cases": [{"value": "z", "target": "zNode"}],
            },
        )
        z_node = WorkflowNode(id="zNode", node_type=NodeType.TASK, worker_type="w")
        def_node = WorkflowNode(id="defNode", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, z_node, def_node])
        edges = [
            WorkflowEdge(source_node_id="sw", target_node_id="zNode"),
            WorkflowEdge(source_node_id="sw", target_node_id="defNode", condition="default"),
        ]
        dag = _topology(edge_map={"sw": edges}, adjacency={"sw": ["zNode", "defNode"]})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"x": "nope"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "defNode" in dispatched_ids


class TestSwitchEventEmission:
    """SWITCH records the same branch-trace observability as CONDITION.
    Phase 4 folded the old ``ConditionEvaluated`` event into ``NodeCompleted``:
    the handler now exposes the branch trace via ``result.completion_payload``
    (``branch`` / ``expression`` / ``condition_result``), which the engine
    merges onto the ``NodeCompleted`` event it emits. An ``EdgeTraversed`` per
    taken edge is emitted downstream by the dispatcher from
    ``taken_edge_ports``. The handler no longer computes a loop
    ``iteration_index`` — the engine stamps that from the task scope stack."""

    @pytest.mark.asyncio
    async def test_emits_condition_evaluated_with_branch(self):
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={"data": [
                {"key": "{{$input.color}}", "operations": "==",
                 "value": "red", "branch": "redBranch"},
                {"key": "{{$input.color}}", "operations": "==",
                 "value": "blue", "branch": "blueBranch"},
            ]},
        )
        red = WorkflowNode(id="r", node_type=NodeType.TASK, worker_type="w")
        blue = WorkflowNode(id="b", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, red, blue])
        edges = [
            WorkflowEdge(source_node_id="sw", target_node_id="r", condition="redBranch"),
            WorkflowEdge(source_node_id="sw", target_node_id="b", condition="blueBranch"),
        ]
        dag = _topology(edge_map={"sw": edges}, adjacency={"sw": ["r", "b"]})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"color": "red"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        # Phase 4: the branch trace is exposed via completion_payload, which
        # the engine merges onto the NodeCompleted event it emits.
        payload = result.completion_payload
        assert payload["expression"] == "{{$input.color}} == red"
        assert payload["condition_result"] is True
        assert payload["branch"] == "redBranch"

        # Phase 3a PR 2: SWITCH/CONDITION handlers no longer append
        # EdgeTraversed to result.events. The handler's contract is to
        # populate ``taken_edge_ports`` (edge_id → port_id);
        # DispatcherConsumer translates that into EdgeTraversed events
        # downstream from the NodeOutputProduced payload. Test that
        # contract here, not the legacy event emission.
        assert len(result.taken_edge_ports) == 1
        # Only the "r" edge (condition="redBranch") should be taken.
        assert result.taken_edge_ports[edges[0].id] == "redBranch"

    @pytest.mark.asyncio
    async def test_selects_taken_edge_port_when_inside_loop(self):
        """A SWITCH running inside a LOOP body still resolves its branch and
        records the taken edge port. Phase 4: the handler no longer computes a
        loop ``iteration_index`` — the engine stamps that onto NodeCompleted
        from the task scope stack — so the handler-level contract here is the
        ``taken_edge_ports`` map the dispatcher consumes."""
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={"data": [
                {"key": "{{$input.color}}", "operations": "==",
                 "value": "red", "branch": "red"},
            ]},
        )
        target = WorkflowNode(id="t", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, target])
        edges = [WorkflowEdge(source_node_id="sw", target_node_id="t", condition="red")]
        dag = _topology(edge_map={"sw": edges}, adjacency={"sw": ["t"]})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={
                "input": {"color": "red"},
                "loop": {"index": 7, "item": "x"},
            }),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        # The edge port was selected so the dispatcher has what it needs to
        # emit the right downstream EdgeTraversed. Loop iteration_index is
        # stamped by the engine from the task scope stack, not the handler.
        assert result.taken_edge_ports[edges[0].id] == "red"

    @pytest.mark.asyncio
    async def test_switch_output_shows_resolved_expression_structured(self):
        """The node output carries the evaluation trace — raw rule, resolved
        rule, evaluated value, matched case — for the output panel."""
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={"data": [
                {"key": "{{$input.color}}", "operations": "==",
                 "value": "red", "branch": "redBranch"},
            ]},
        )
        target = WorkflowNode(id="t", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, target])
        edges = [WorkflowEdge(source_node_id="sw", target_node_id="t", condition="redBranch")]
        dag = _topology(edge_map={"sw": edges}, adjacency={"sw": ["t"]})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"color": "red"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        out = result.state_updates["sw"]["output"]
        assert out["evaluated_value"] == "red"
        assert out["matched_case"] == "redBranch"
        assert out["branch"] == "redBranch"
        assert out["expression"] == "{{$input.color}} == red"
        assert out["resolved"] == "red == red"

    @pytest.mark.asyncio
    async def test_switch_output_shows_resolved_expression_legacy(self):
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={
                "expression": "{{$input.color}}",
                "cases": [{"value": "red", "target": "t"}],
            },
        )
        target = WorkflowNode(id="t", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, target])
        edges = [WorkflowEdge(source_node_id="sw", target_node_id="t")]
        dag = _topology(edge_map={"sw": edges}, adjacency={"sw": ["t"]})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"color": "red"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        out = result.state_updates["sw"]["output"]
        assert out["evaluated_value"] == "red"
        assert out["matched_case"] == "t"
        assert out["expression"] == "{{$input.color}}"
        assert out["resolved"] == "red"

    @pytest.mark.asyncio
    async def test_emits_condition_evaluated_for_legacy_expression_mode(self):
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={
                "expression": "{{$input.color}}",
                "cases": [{"value": "red", "target": "rNode"}],
                "default": "dNode",
            },
        )
        r_node = WorkflowNode(id="rNode", node_type=NodeType.TASK, worker_type="w")
        d_node = WorkflowNode(id="dNode", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, r_node, d_node])
        edges = [
            WorkflowEdge(source_node_id="sw", target_node_id="rNode"),
            WorkflowEdge(source_node_id="sw", target_node_id="dNode", condition="default"),
        ]
        dag = _topology(edge_map={"sw": edges}, adjacency={"sw": ["rNode", "dNode"]})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"color": "red"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        payload = result.completion_payload
        assert payload["expression"] == "{{$input.color}}"
        assert payload["condition_result"] is True
        # Legacy mode falls back to matched_target as branch label.
        assert payload["branch"] == "rNode"

    @pytest.mark.asyncio
    async def test_emits_condition_evaluated_with_empty_branch_when_no_match(self):
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={"data": [
                {"key": "{{$input.color}}", "operations": "==",
                 "value": "red", "branch": "redBranch"},
            ]},
        )
        target = WorkflowNode(id="r", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, target])
        edges = [WorkflowEdge(source_node_id="sw", target_node_id="r", condition="redBranch")]
        dag = _topology(edge_map={"sw": edges}, adjacency={"sw": ["r"]})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"color": "blue"}}),  # no rule matches
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        payload = result.completion_payload
        # No match: branch is empty, result is False, expression falls back
        # to the first rule's full comparison expression for debug context.
        assert payload["branch"] == ""
        assert payload["condition_result"] is False
        assert payload["expression"] == "{{$input.color}} == red"
        # No edges taken — handler's contract under Phase 3a PR 2 is the
        # taken_edge_ports map, not handler-emitted EdgeTraversed events.
        assert result.taken_edge_ports == {}

    @pytest.mark.asyncio
    async def test_routes_via_rule_port_id_when_edges_keyed_by_uuid(self):
        """Frontends that build SWITCH out-ports keyed by UUID stamp the
        target ``port_id`` onto each rule. The handler must prefer that
        UUID over the human-readable ``branch`` label, otherwise edges
        whose ``source_port_id`` is the UUID never match and every input
        falls through to the default port."""
        handler = SwitchNodeHandler(variable_resolver=_resolver())
        port_b1 = "be90c2a4-b342-41c6-bad3-58ccf0069ac9"
        port_b2 = "ec5268be-7517-4866-88d9-a77f9174af8a"
        node = WorkflowNode(
            id="sw", node_type=NodeType.SWITCH,
            config={"data": [
                {"key": "{{$input.value}}", "operations": "==",
                 "value": "1", "branch": "branch_1", "port_id": port_b1},
                {"key": "{{$input.value}}", "operations": "==",
                 "value": "2", "branch": "branch_2", "port_id": port_b2},
            ]},
        )
        n1 = WorkflowNode(id="n1", node_type=NodeType.TASK, worker_type="w")
        n2 = WorkflowNode(id="n2", node_type=NodeType.TASK, worker_type="w")
        nd = WorkflowNode(id="nd", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, n1, n2, nd])
        edges = [
            WorkflowEdge(source_node_id="sw", target_node_id="n1", source_port_id=port_b1),
            WorkflowEdge(source_node_id="sw", target_node_id="n2", source_port_id=port_b2),
            WorkflowEdge(source_node_id="sw", target_node_id="nd", source_port_id="default"),
        ]
        dag = _topology(edge_map={"sw": edges}, adjacency={"sw": ["n1", "n2", "nd"]})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"value": "2"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        # Only the edge whose source_port_id matches the rule's port_id
        # (branch_2 / port_b2) is taken. The default edge stays skipped.
        assert len(result.taken_edge_ports) == 1
        assert result.taken_edge_ports[edges[1].id] == port_b2
        assert result.completion_payload["branch"] == "branch_2"


# ────────────── LoopHandler ──────────────


class TestLoopHandler:
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    async def test_initialize_loop_sequential(self):
        handler = LoopNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="loop", node_type=NodeType.LOOP,
            config={"collection": "{{$input.items}}", "mode": "sequential"},
        )
        body_node = WorkflowNode(id="body", node_type=NodeType.TASK, worker_type="w")
        exit_node = WorkflowNode(id="exit", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, body_node, exit_node])
        edges = [
            WorkflowEdge(source_node_id="loop", target_node_id="body", condition="body"),
            WorkflowEdge(source_node_id="loop", target_node_id="exit", condition="exit"),
        ]
        dag = _topology(edge_map={"loop": edges})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"items": ["a", "b", "c"]}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert len(result.tasks_to_dispatch) == 1
        assert result.tasks_to_dispatch[0].iteration_index == 0
        assert result.loop_state_updates["loop"]["total"] == 3
        assert result.state_updates["loop"]["index"] == 0

    @pytest.mark.asyncio
    async def test_initialize_loop_parallel(self):
        handler = LoopNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="loop", node_type=NodeType.LOOP,
            config={"collection": "{{$input.items}}", "mode": "parallel", "parallelism": 2},
        )
        body_node = WorkflowNode(id="body", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, body_node])
        edges = [WorkflowEdge(source_node_id="loop", target_node_id="body", condition="body")]
        dag = _topology(edge_map={"loop": edges})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"items": ["a", "b", "c"]}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert len(result.tasks_to_dispatch) == 2  # parallelism=2

    @pytest.mark.asyncio
    async def test_empty_items_dispatches_exit(self):
        handler = LoopNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="loop", node_type=NodeType.LOOP,
            config={"collection": "{{$input.items}}"},
        )
        exit_node = WorkflowNode(id="exit", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, exit_node])
        edges = [WorkflowEdge(source_node_id="loop", target_node_id="exit", condition="exit")]
        dag = _topology(edge_map={"loop": edges})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"items": []}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "exit" in dispatched_ids

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    async def test_iteration_complete_sequential(self):
        handler = LoopNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="loop", node_type=NodeType.LOOP,
            config={"collection": "{{$input.items}}", "mode": "sequential"},
        )
        body_node = WorkflowNode(id="body", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, body_node])
        edges = [WorkflowEdge(source_node_id="loop", target_node_id="body", condition="body")]
        dag = _topology(edge_map={"loop": edges})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(
                variables={"input": {"items": ["a", "b"]}},
                loop_state={
                    "loop": {
                        "items": ["a", "b"],
                        "current_index": 0,
                        "total": 2,
                        "completed_iterations": [],
                        "failed_iterations": [],
                        "mode": "sequential",
                    }
                },
            ),
        )
        completed_task = Task(id="t1", run_id="r1", node_id="body", iteration_index=0)

        result = await handler.handle(node, run, workflow, dag, completed_task, _factory())

        assert len(result.tasks_to_dispatch) == 1
        assert result.tasks_to_dispatch[0].iteration_index == 1
        assert len(result.events) == 1

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    async def test_all_iterations_complete_dispatches_exit(self):
        handler = LoopNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="loop", node_type=NodeType.LOOP,
            config={"collection": "{{$input.items}}"},
        )
        exit_node = WorkflowNode(id="exit", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, exit_node])
        edges = [
            WorkflowEdge(source_node_id="loop", target_node_id="exit", condition="exit"),
        ]
        dag = _topology(edge_map={"loop": edges})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(
                variables={"input": {"items": ["a"]}},
                loop_state={
                    "loop": {
                        "items": ["a"],
                        "current_index": 0,
                        "total": 1,
                        "completed_iterations": [],
                        "failed_iterations": [],
                        "mode": "sequential",
                    }
                },
            ),
        )
        completed_task = Task(id="t1", run_id="r1", node_id="body", iteration_index=0)

        result = await handler.handle(node, run, workflow, dag, completed_task, _factory())

        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "exit" in dispatched_ids

    @pytest.mark.asyncio
    async def test_max_iterations_truncation(self):
        handler = LoopNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="loop", node_type=NodeType.LOOP,
            config={"collection": "{{$input.items}}", "max_iterations": 2},
        )
        body_node = WorkflowNode(id="body", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, body_node])
        edges = [WorkflowEdge(source_node_id="loop", target_node_id="body", condition="body")]
        dag = _topology(edge_map={"loop": edges})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"items": [1, 2, 3, 4, 5]}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert result.loop_state_updates["loop"]["total"] == 2

    @pytest.mark.asyncio
    async def test_resolution_error_empty_items(self):
        handler = LoopNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="loop", node_type=NodeType.LOOP,
            config={"collection": "{{$missing.items}}"},
        )
        exit_node = WorkflowNode(id="exit", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, exit_node])
        edges = [WorkflowEdge(source_node_id="loop", target_node_id="exit", condition="exit")]
        dag = _topology(edge_map={"loop": edges})
        run = WorkflowRun(id="r1", workflow_id="wf", context=RunContext())

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        # Error resolving -> empty items -> dispatch exit
        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "exit" in dispatched_ids

    @pytest.mark.asyncio
    async def test_body_entry_fallback_non_exit(self):
        """When no edge has 'body' condition, pick the first non-exit edge."""
        handler = LoopNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="loop", node_type=NodeType.LOOP,
            config={"collection": "{{$input.items}}"},
        )
        body_node = WorkflowNode(id="body", node_type=NodeType.TASK, worker_type="w")
        exit_node = WorkflowNode(id="exit", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, body_node, exit_node])
        edges = [
            WorkflowEdge(source_node_id="loop", target_node_id="body"),
            WorkflowEdge(source_node_id="loop", target_node_id="exit", condition="exit"),
        ]
        dag = _topology(edge_map={"loop": edges})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"items": ["a"]}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert len(result.tasks_to_dispatch) == 1
        assert result.tasks_to_dispatch[0].node_id == "body"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    async def test_parallel_iteration_complete_dispatches_next(self):
        handler = LoopNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="loop", node_type=NodeType.LOOP,
            config={"collection": "{{$input.items}}", "mode": "parallel", "parallelism": 1},
        )
        body_node = WorkflowNode(id="body", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, body_node])
        edges = [WorkflowEdge(source_node_id="loop", target_node_id="body", condition="body")]
        dag = _topology(edge_map={"loop": edges})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(
                variables={"input": {"items": ["a", "b"]}},
                loop_state={
                    "loop": {
                        "items": ["a", "b"],
                        "current_index": 0,
                        "total": 2,
                        "completed_iterations": [],
                        "failed_iterations": [],
                        "mode": "parallel",
                        "parallelism": 1,
                    }
                },
            ),
        )
        completed_task = Task(id="t1", run_id="r1", node_id="body", iteration_index=0)

        result = await handler.handle(node, run, workflow, dag, completed_task, _factory())

        assert len(result.tasks_to_dispatch) == 1
        assert result.tasks_to_dispatch[0].iteration_index == 1


# ────────────── ParallelHandler ──────────────


class TestParallelHandler:
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_fan_out_via_edges(self):
        handler = ParallelNodeHandler()
        node = WorkflowNode(id="par", node_type=NodeType.PARALLEL, config={})
        branch_a = WorkflowNode(id="a", node_type=NodeType.TASK, worker_type="w")
        branch_b = WorkflowNode(id="b", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, branch_a, branch_b])
        edges = [
            WorkflowEdge(source_node_id="par", target_node_id="a"),
            WorkflowEdge(source_node_id="par", target_node_id="b"),
        ]
        dag = _topology(edge_map={"par": edges})
        run = WorkflowRun(id="r1", workflow_id="wf")

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert dispatched_ids == {"a", "b"}

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_fan_out_via_branches_config(self):
        handler = ParallelNodeHandler()
        node = WorkflowNode(
            id="par", node_type=NodeType.PARALLEL,
            config={"branches": [
                {"nodes": ["a"]},
                {"nodes": ["b"]},
            ]},
        )
        branch_a = WorkflowNode(id="a", node_type=NodeType.TASK, worker_type="w")
        branch_b = WorkflowNode(id="b", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, branch_a, branch_b])
        dag = _topology(edge_map={"par": []})
        run = WorkflowRun(id="r1", workflow_id="wf")

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        dispatched_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert dispatched_ids == {"a", "b"}

    @pytest.mark.asyncio
    async def test_conditional_branch_skipped(self):
        """A gated-OFF branch (condition resolves falsy) must NOT take its
        outgoing edge AND must mark its nodes skipped, so a downstream MERGE
        counts them toward quorum instead of waiting on a branch that never
        arrives (bug #12: gated PARALLEL + MERGE wait_all hangs)."""
        handler = ParallelNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="par", node_type=NodeType.PARALLEL,
            config={"branches": [
                {"condition": "{{$input.skip}}", "nodes": ["a"]},  # falsy → off
                {"nodes": ["b"]},                                   # no cond → on
            ]},
        )
        branch_a = WorkflowNode(id="a", node_type=NodeType.TASK, worker_type="w")
        branch_b = WorkflowNode(id="b", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, branch_a, branch_b])
        a_edge = WorkflowEdge(id="e_a", source_node_id="par", target_node_id="a")
        b_edge = WorkflowEdge(id="e_b", source_node_id="par", target_node_id="b")
        dag = _topology(edge_map={"par": [a_edge, b_edge]})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"skip": False}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        # Gated-ON branch "b" is taken; gated-OFF branch "a" is not.
        assert b_edge.id in result.taken_edge_ports
        assert a_edge.id not in result.taken_edge_ports
        # The gated-OFF branch's nodes are marked skipped.
        assert "a" in result.skipped_node_ids
        assert "b" not in result.skipped_node_ids

    @pytest.mark.asyncio
    async def test_branch_condition_resolution_error(self):
        """A branch whose condition fails to resolve is treated as gated-OFF:
        its outgoing edge is not added to ``taken_edge_ports`` AND its nodes
        are marked skipped (so a downstream MERGE doesn't hang waiting on a
        branch that will never run). Previously this produced no skip list,
        which left the branch orphaned — bug #12."""
        handler = ParallelNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="par", node_type=NodeType.PARALLEL,
            config={"branches": [
                {"condition": "{{$missing.x}}", "nodes": ["a"]},
            ]},
        )
        branch_a = WorkflowNode(id="a", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, branch_a])
        a_edge = WorkflowEdge(id="e_a", source_node_id="par", target_node_id="a")
        dag = _topology(edge_map={"par": [a_edge]})
        run = WorkflowRun(id="r1", workflow_id="wf", context=RunContext())

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert result.skipped_node_ids == ["a"]
        assert a_edge.id not in result.taken_edge_ports

    @pytest.mark.asyncio
    async def test_empty_branch_nodes_skipped(self):
        handler = ParallelNodeHandler()
        node = WorkflowNode(
            id="par", node_type=NodeType.PARALLEL,
            config={"branches": [{"nodes": []}]},
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology(edge_map={"par": []})
        run = WorkflowRun(id="r1", workflow_id="wf")

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert len(result.tasks_to_dispatch) == 0


# ────────────── MergeHandler ──────────────


class TestMergeHandler:
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_all_predecessors_done(self):
        handler = MergeNodeHandler()
        node = WorkflowNode(
            id="merge", node_type=NodeType.MERGE,
            config={"wait_strategy": "all"},
        )
        next_node = WorkflowNode(id="next", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, next_node])
        edges = [WorkflowEdge(source_node_id="merge", target_node_id="next")]
        dag = _topology(
            edge_map={"merge": edges},
            predecessors={"merge": ["a", "b"]},
        )
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(
                completed_nodes={"a", "b"},
                variables={"a": {"data": 1}, "b": {"data": 2}},
            ),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert len(result.tasks_to_dispatch) == 1
        assert "merge" in result.state_updates

    @pytest.mark.asyncio
    async def test_not_all_done_returns_empty(self):
        handler = MergeNodeHandler()
        node = WorkflowNode(id="merge", node_type=NodeType.MERGE, config={"wait_strategy": "all"})
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology(predecessors={"merge": ["a", "b"]})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(completed_nodes={"a"}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert len(result.tasks_to_dispatch) == 0

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_any_wait_strategy(self):
        handler = MergeNodeHandler()
        node = WorkflowNode(id="merge", node_type=NodeType.MERGE, config={"wait_strategy": "any"})
        next_node = WorkflowNode(id="next", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, next_node])
        edges = [WorkflowEdge(source_node_id="merge", target_node_id="next")]
        dag = _topology(
            edge_map={"merge": edges},
            predecessors={"merge": ["a", "b"]},
        )
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(completed_nodes={"a"}, variables={"a": {}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert len(result.tasks_to_dispatch) == 1

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_n_of_m_strategy(self):
        handler = MergeNodeHandler()
        node = WorkflowNode(
            id="merge", node_type=NodeType.MERGE,
            config={"wait_strategy": "n_of_m", "n_required": 2},
        )
        next_node = WorkflowNode(id="next", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, next_node])
        edges = [WorkflowEdge(source_node_id="merge", target_node_id="next")]
        dag = _topology(
            edge_map={"merge": edges},
            predecessors={"merge": ["a", "b", "c"]},
        )
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(completed_nodes={"a", "b"}, variables={"a": {}, "b": {}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert len(result.tasks_to_dispatch) == 1

    @pytest.mark.asyncio
    async def test_inputs_config(self):
        handler = MergeNodeHandler()
        node = WorkflowNode(
            id="merge", node_type=NodeType.MERGE,
            config={
                "wait_strategy": "all",
                "inputs": [
                    {"source": "a", "key": "from_a"},
                    {"source": "b", "key": "from_b"},
                ],
            },
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology(predecessors={"merge": ["a", "b"]}, edge_map={"merge": []})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(
                completed_nodes={"a", "b"},
                variables={"a": {"val": 1}, "b": {"val": 2}},
            ),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        merged = result.state_updates["merge"]["output"]
        assert merged["from_a"] == {"val": 1}
        assert merged["from_b"] == {"val": 2}

    @pytest.mark.asyncio
    async def test_skipped_predecessor_uses_defaults(self):
        handler = MergeNodeHandler()
        node = WorkflowNode(
            id="merge", node_type=NodeType.MERGE,
            config={
                "wait_strategy": "all",
                "inputs": [{"source": "a", "key": "from_a"}],
                "defaults": {"from_a": {"fallback": True}},
            },
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology(predecessors={"merge": ["a"]}, edge_map={"merge": []})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(skipped_nodes={"a"}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        merged = result.state_updates["merge"]["output"]
        assert merged["from_a"] == {"fallback": True}

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        handler = MergeNodeHandler()
        node = WorkflowNode(
            id="merge", node_type=NodeType.MERGE,
            config={
                "wait_strategy": "all",
                "on_branch_failure": "partial",
                "inputs": [
                    {"source": "a", "key": "from_a", "required": True},
                ],
                "defaults": {"from_a": {"default": True}},
            },
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology(predecessors={"merge": ["a"]}, edge_map={"merge": []})
        # a is neither completed nor skipped but on_branch_failure=partial
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(completed_nodes=set()),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())
        # Not all predecessors done -> should_proceed=False
        assert len(result.tasks_to_dispatch) == 0

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_unknown_wait_strategy_defaults_all(self):
        handler = MergeNodeHandler()
        node = WorkflowNode(
            id="merge", node_type=NodeType.MERGE,
            config={"wait_strategy": "unknown_strategy"},
        )
        next_node = WorkflowNode(id="next", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, next_node])
        edges = [WorkflowEdge(source_node_id="merge", target_node_id="next")]
        dag = _topology(
            edge_map={"merge": edges},
            predecessors={"merge": ["a"]},
        )
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(completed_nodes={"a"}, variables={"a": {}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())
        assert len(result.tasks_to_dispatch) == 1

    @pytest.mark.asyncio
    async def test_inputs_not_required_uses_defaults(self):
        """Lines 59-60: source not completed and not required -> use defaults."""
        handler = MergeNodeHandler()
        node = WorkflowNode(
            id="merge", node_type=NodeType.MERGE,
            config={
                "wait_strategy": "all",
                "inputs": [
                    {"source": "a", "key": "from_a", "required": False},
                ],
                "defaults": {"from_a": {"default_val": True}},
            },
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology(predecessors={"merge": ["a"]}, edge_map={"merge": []})
        # a is completed (so "all" passes), but the data-gather checks source in completed
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(completed_nodes={"a"}, variables={}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        merged = result.state_updates["merge"]["output"]
        # a is in completed but not in variables -> get returns {}
        assert "from_a" in merged

    @pytest.mark.asyncio
    async def test_inputs_required_partial_failure(self):
        """Lines 61-62: source not completed, required=True, on_branch_failure=partial."""
        handler = MergeNodeHandler()
        node = WorkflowNode(
            id="merge", node_type=NodeType.MERGE,
            config={
                "wait_strategy": "all",
                "on_branch_failure": "partial",
                "inputs": [
                    {"source": "a", "key": "from_a", "required": True},
                    {"source": "b", "key": "from_b", "required": True},
                ],
                "defaults": {"from_a": {"fallback": True}, "from_b": {"fallback": True}},
            },
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology(predecessors={"merge": ["a", "b"]}, edge_map={"merge": []})
        # Only a is completed; b is in skipped
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(
                completed_nodes={"a"},
                skipped_nodes={"b"},
                variables={"a": {"val": 1}},
            ),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        merged = result.state_updates["merge"]["output"]
        assert merged["from_a"] == {"val": 1}
        assert merged["from_b"] == {"fallback": True}  # b was skipped

    @pytest.mark.asyncio
    async def test_no_inputs_config_skipped_predecessor(self):
        """Line 68: predecessor in skipped_nodes -> merged_data[pred_id] = {}."""
        handler = MergeNodeHandler()
        node = WorkflowNode(id="merge", node_type=NodeType.MERGE, config={"wait_strategy": "all"})
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology(predecessors={"merge": ["a", "b"]}, edge_map={"merge": []})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(
                completed_nodes={"a"},
                skipped_nodes={"b"},
                variables={"a": {"v": 1}},
            ),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        merged = result.state_updates["merge"]["output"]
        assert merged["a"] == {"v": 1}
        assert merged["b"] == {}

    @pytest.mark.asyncio
    async def test_no_inputs_config_uses_predecessors(self):
        handler = MergeNodeHandler()
        node = WorkflowNode(id="merge", node_type=NodeType.MERGE, config={"wait_strategy": "all"})
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology(predecessors={"merge": ["a", "b"]}, edge_map={"merge": []})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(
                completed_nodes={"a", "b"},
                variables={"a": {"v": 1}, "b": {"v": 2}},
            ),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        merged = result.state_updates["merge"]["output"]
        assert "a" in merged
        assert "b" in merged

    # ───────── Phase 4c.4.4 scope-aware quorum buffers ─────────

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_scope_aware_no_scope_uses_sentinel_key(self):
        """Legacy path: completed task has no scope_stack. Buffer key
        falls back to the ``(node_id, "", -1)`` sentinel so a single
        buffer covers the merge for the whole run — exactly today's
        semantics."""
        from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler as MH
        handler = MH()
        node = WorkflowNode(
            id="merge", node_type=NodeType.MERGE,
            config={"wait_strategy": "all"},
        )
        next_node = WorkflowNode(id="next", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, next_node])
        dag = _topology(
            edge_map={"merge": [WorkflowEdge(source_node_id="merge", target_node_id="next")]},
            predecessors={"merge": ["a", "b"]},
        )
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(
                completed_nodes={"a", "b"},
                variables={"a": {}, "b": {}},
            ),
        )
        # No completed_task — legacy call shape.
        result = await handler.handle(node, run, workflow, dag, None, _factory())
        assert len(result.tasks_to_dispatch) == 1
        # Sentinel key landed in the buffer.
        assert ("merge", "", -1) in run.context.merge_quorum_buffers
        assert run.context.merge_quorum_buffers[("merge", "", -1)] == {"a", "b"}

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_scope_aware_with_scope_keys_by_invocation_and_iter(self):
        """Event-driven path: completed task carries a scope. Buffer
        key includes ``(invocation_id, iter)``. A second task from a
        different iter gets a fresh buffer."""
        from app.layer1_domain.value_objects.loop_scope import LoopScope
        from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler as MH
        handler = MH()
        node = WorkflowNode(
            id="merge", node_type=NodeType.MERGE,
            config={"wait_strategy": "all"},
        )
        next_node = WorkflowNode(id="next", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, next_node])
        dag = _topology(
            edge_map={"merge": [WorkflowEdge(source_node_id="merge", target_node_id="next")]},
            predecessors={"merge": ["a", "b"]},
        )
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(
                # Both preds done — mimics end of iter 0 after both
                # branches of the parallel body completed.
                completed_nodes={"a", "b"},
                variables={"a": {}, "b": {}},
            ),
        )
        scope_iter_0 = LoopScope("loop-1", "inv-1", iter=0, total=3)
        body_task = Task(
            id="t0", run_id="r1", node_id="b",
            scope_stack=[scope_iter_0],
        )
        result = await handler.handle(node, run, workflow, dag, body_task, _factory())
        assert len(result.tasks_to_dispatch) == 1
        # Buffer keyed on (merge_id, invocation_id, iter).
        assert ("merge", "inv-1", 0) in run.context.merge_quorum_buffers
        # Now the same merge fires again in iter=1 — it gets a FRESH
        # buffer because the key differs.
        scope_iter_1 = LoopScope("loop-1", "inv-1", iter=1, total=3)
        body_task_1 = Task(
            id="t1", run_id="r1", node_id="b",
            scope_stack=[scope_iter_1],
        )
        result_1 = await handler.handle(node, run, workflow, dag, body_task_1, _factory())
        # The buffer for iter=1 also has a and b (seeded from
        # completed_nodes), so quorum fires again. Critically the two
        # buffers are DIFFERENT keys → re-arming works.
        assert ("merge", "inv-1", 1) in run.context.merge_quorum_buffers
        assert len(result_1.tasks_to_dispatch) == 1

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_scope_aware_parallel_iterations_are_isolated(self):
        """Two in-flight parallel iterations: one has both preds done,
        the other has only one. Only the first iteration's merge fires."""
        from app.layer1_domain.value_objects.loop_scope import LoopScope
        from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler as MH
        handler = MH()
        node = WorkflowNode(
            id="merge", node_type=NodeType.MERGE,
            config={"wait_strategy": "all"},
        )
        next_node = WorkflowNode(id="next", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, next_node])
        dag = _topology(
            edge_map={"merge": [WorkflowEdge(source_node_id="merge", target_node_id="next")]},
            predecessors={"merge": ["a", "b"]},
        )
        # In a parallel loop both iterations see the same run context,
        # so we simulate per-iter progress by manipulating the buffers
        # directly (mirroring what the legacy walker would do via
        # .discard, but scope-aware). Start with ``completed_nodes``
        # empty to force buffer-based quorum.
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"a": {}, "b": {}}),
        )
        # iter=0: pred "a" completes
        scope_0 = LoopScope("loop-1", "inv-1", iter=0, total=2)
        r = await handler.handle(
            node, run, workflow, dag,
            Task(id="a0", run_id="r1", node_id="a", scope_stack=[scope_0]),
            _factory(),
        )
        assert len(r.tasks_to_dispatch) == 0, "only one pred done → no quorum yet"
        # iter=1: pred "a" completes
        scope_1 = LoopScope("loop-1", "inv-1", iter=1, total=2)
        r = await handler.handle(
            node, run, workflow, dag,
            Task(id="a1", run_id="r1", node_id="a", scope_stack=[scope_1]),
            _factory(),
        )
        assert len(r.tasks_to_dispatch) == 0, "iter=1 also only one pred"
        # iter=0: pred "b" completes → quorum fires for iter 0
        r = await handler.handle(
            node, run, workflow, dag,
            Task(id="b0", run_id="r1", node_id="b", scope_stack=[scope_0]),
            _factory(),
        )
        assert len(r.tasks_to_dispatch) == 1, "iter=0 quorum hit"
        # iter=1 buffer still only has "a"
        assert run.context.merge_quorum_buffers[("merge", "inv-1", 1)] == {"a"}
        # iter=1: pred "b" completes → quorum fires for iter 1
        r = await handler.handle(
            node, run, workflow, dag,
            Task(id="b1", run_id="r1", node_id="b", scope_stack=[scope_1]),
            _factory(),
        )
        assert len(r.tasks_to_dispatch) == 1, "iter=1 quorum hit"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_scope_aware_nested_loop_different_invocation_ids(self):
        """Outer loop × 2 × inner loop with a merge inside the inner
        body. Each outer iteration gets a different inner ``invocation_id``,
        so the inner merge's quorum buffer is fresh per outer iter."""
        from app.layer1_domain.value_objects.loop_scope import LoopScope
        from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler as MH
        handler = MH()
        node = WorkflowNode(
            id="merge", node_type=NodeType.MERGE,
            config={"wait_strategy": "all"},
        )
        next_node = WorkflowNode(id="next", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, next_node])
        dag = _topology(
            edge_map={"merge": [WorkflowEdge(source_node_id="merge", target_node_id="next")]},
            predecessors={"merge": ["a", "b"]},
        )
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"a": {}, "b": {}}),
        )
        # Outer iter 0, inner invocation INNER_0, inner iter 0 — both preds done.
        outer_0 = LoopScope("loop-outer", "OUTER", iter=0, total=2)
        inner_in_outer_0 = LoopScope("loop-inner", "INNER_0", iter=0, total=1)
        await handler.handle(
            node, run, workflow, dag,
            Task(id="a0", run_id="r1", node_id="a",
                 scope_stack=[outer_0, inner_in_outer_0]),
            _factory(),
        )
        r = await handler.handle(
            node, run, workflow, dag,
            Task(id="b0", run_id="r1", node_id="b",
                 scope_stack=[outer_0, inner_in_outer_0]),
            _factory(),
        )
        assert len(r.tasks_to_dispatch) == 1
        assert ("merge", "INNER_0", 0) in run.context.merge_quorum_buffers

        # Outer iter 1 — inner loop gets a DIFFERENT invocation_id.
        outer_1 = LoopScope("loop-outer", "OUTER", iter=1, total=2)
        inner_in_outer_1 = LoopScope("loop-inner", "INNER_1", iter=0, total=1)
        # Only pred "a" done for the new invocation — quorum should NOT
        # fire, because the buffer for INNER_1 is fresh.
        r = await handler.handle(
            node, run, workflow, dag,
            Task(id="a1", run_id="r1", node_id="a",
                 scope_stack=[outer_1, inner_in_outer_1]),
            _factory(),
        )
        assert len(r.tasks_to_dispatch) == 0, (
            "outer iter 1's inner merge buffer must be fresh — it "
            "should not inherit outer iter 0's completions"
        )
        assert ("merge", "INNER_1", 0) in run.context.merge_quorum_buffers
        assert run.context.merge_quorum_buffers[("merge", "INNER_1", 0)] == {"a"}
        # Complete "b" for INNER_1 → quorum fires.
        r = await handler.handle(
            node, run, workflow, dag,
            Task(id="b1", run_id="r1", node_id="b",
                 scope_stack=[outer_1, inner_in_outer_1]),
            _factory(),
        )
        assert len(r.tasks_to_dispatch) == 1


# ────────────── WorkflowHandler ──────────────


class TestWorkflowHandler:
    @pytest.mark.asyncio
    async def test_basic_workflow_node(self):
        handler = WorkflowNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="wfnode", node_type=NodeType.WORKFLOW,
            config={"workflow_id": "child-wf", "input_mapping": {"key": "{{$input.data}}"}},
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"data": "hello"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert len(result.tasks_to_dispatch) == 1
        state = result.state_updates["wfnode"]
        assert state["metadata"]["child_workflow_id"] == "child-wf"
        assert state["metadata"]["child_input"] == {"key": "hello"}

    @pytest.mark.asyncio
    async def test_max_depth_exceeded(self):
        handler = WorkflowNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="wfnode", node_type=NodeType.WORKFLOW,
            config={"workflow_id": "child-wf", "max_depth": 2},
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf", depth_level=5)

        with pytest.raises(MaxDepthExceededError):
            await handler.handle(node, run, workflow, dag, None, _factory())

    @pytest.mark.asyncio
    async def test_missing_workflow_id(self):
        handler = WorkflowNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="wfnode", node_type=NodeType.WORKFLOW,
            config={},
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf")

        with pytest.raises(ExpressionError, match="missing config.workflow_id"):
            await handler.handle(node, run, workflow, dag, None, _factory())

    @pytest.mark.asyncio
    async def test_input_binding_resolution_error(self):
        handler = WorkflowNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="wfnode", node_type=NodeType.WORKFLOW,
            config={"workflow_id": "child-wf", "input_mapping": {"key": "{{$missing.data}}"}},
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf", context=RunContext())

        with pytest.raises(ExpressionError, match="input mapping resolution failed"):
            await handler.handle(node, run, workflow, dag, None, _factory())

    @pytest.mark.asyncio
    async def test_no_input_binding(self):
        handler = WorkflowNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="wfnode", node_type=NodeType.WORKFLOW,
            config={"workflow_id": "child-wf"},
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf")

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert result.state_updates["wfnode"]["metadata"]["child_input"] == {}

    @pytest.mark.asyncio
    async def test_predefined_run_id_is_deterministic(self):
        """The child-run id is deterministic — no wall-clock component — so a
        redelivered/replayed WORKFLOW dispatch reuses the same child run id
        instead of spawning an orphaned duplicate. ``generation`` bumps on
        reset/re-execute, so a genuine new execution gets a fresh child run.
        """
        handler = WorkflowNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="wfnode", node_type=NodeType.WORKFLOW,
            config={"workflow_id": "child-wf"},
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology()
        run = WorkflowRun(id="r1", workflow_id="wf")

        first = await handler.handle(node, run, workflow, dag, None, _factory())
        second = await handler.handle(node, run, workflow, dag, None, _factory())

        id_first = first.state_updates["wfnode"]["metadata"]["predefined_run_id"]
        id_second = second.state_updates["wfnode"]["metadata"]["predefined_run_id"]
        # uuid5-hashed (not the legacy ``{run}_{node}_{gen}`` concatenation),
        # so it stays a fixed 36 chars at any nesting depth instead of growing
        # ~39 chars/level. Assert the invariants, not the literal string.
        assert len(id_first) == 36  # bounded — does not explode with depth
        assert id_first == id_second  # deterministic: redelivery → same id

        # A reset/re-execute bumps generation → a fresh, distinct child run id.
        run.generation = 1
        bumped = await handler.handle(node, run, workflow, dag, None, _factory())
        bumped_id = bumped.state_updates["wfnode"]["metadata"]["predefined_run_id"]
        assert bumped_id != id_first  # generation-sensitive
        assert len(bumped_id) == 36


# ────────────── ComputeHandler ──────────────


class TestComputeHandler:
    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Handler no longer populates tasks_to_dispatch — routing by engine _route_to_successors")
    async def test_basic_computation(self):
        handler = ComputeNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="comp", node_type=NodeType.COMPUTE,
            config={"expression": "{{$input.a}}", "result_key": "total"},
        )
        next_node = WorkflowNode(id="next", node_type=NodeType.TASK, worker_type="w")
        workflow = Workflow(id="wf", nodes=[node, next_node])
        edges = [WorkflowEdge(source_node_id="comp", target_node_id="next")]
        dag = _topology(edge_map={"comp": edges})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"a": "2 + 3"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert result.state_updates["comp"]["output"]["result"] == 5
        assert result.state_updates["total"] == 5
        assert len(result.tasks_to_dispatch) == 1

    @pytest.mark.asyncio
    async def test_invalid_expression(self):
        handler = ComputeNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="comp", node_type=NodeType.COMPUTE,
            config={"expression": "{{$input.val}}"},
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology(edge_map={"comp": []})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"val": "import os"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        computed = result.state_updates["comp"]["output"]["result"]
        assert isinstance(computed, dict) and computed.get("_error") is True

    @pytest.mark.asyncio
    async def test_no_result_key(self):
        handler = ComputeNodeHandler(variable_resolver=_resolver())
        node = WorkflowNode(
            id="comp", node_type=NodeType.COMPUTE,
            config={"expression": "{{$input.a}}"},
        )
        workflow = Workflow(id="wf", nodes=[node])
        dag = _topology(edge_map={"comp": []})
        run = WorkflowRun(
            id="r1", workflow_id="wf",
            context=RunContext(variables={"input": {"a": "1 + 1"}}),
        )

        result = await handler.handle(node, run, workflow, dag, None, _factory())

        assert "total" not in result.state_updates
        assert result.state_updates["comp"]["output"]["result"] == 2


class TestSafeEval:
    def test_addition(self):
        assert _safe_eval("2 + 3") == 5

    def test_subtraction(self):
        assert _safe_eval("10 - 3") == 7

    def test_multiplication(self):
        assert _safe_eval("4 * 5") == 20

    def test_division(self):
        assert _safe_eval("10 / 4") == 2.5

    def test_floor_division(self):
        assert _safe_eval("10 // 3") == 3

    def test_modulo(self):
        assert _safe_eval("10 % 3") == 1

    def test_power(self):
        assert _safe_eval("2 ** 3") == 8

    def test_unary_neg(self):
        assert _safe_eval("-5") == -5

    def test_unary_pos(self):
        assert _safe_eval("+5") == 5

    def test_comparison(self):
        assert _safe_eval("3 < 5") is True
        assert _safe_eval("5 == 5") is True
        assert _safe_eval("5 != 3") is True
        assert _safe_eval("5 > 3") is True
        assert _safe_eval("3 <= 3") is True
        assert _safe_eval("3 >= 3") is True

    def test_boolean_and(self):
        assert _safe_eval("True and True") is True
        assert _safe_eval("True and False") is False

    def test_boolean_or(self):
        assert _safe_eval("False or True") is True

    def test_if_expression(self):
        assert _safe_eval("1 if True else 2") == 1
        assert _safe_eval("1 if False else 2") == 2

    def test_function_call_abs(self):
        assert _safe_eval("abs(-5)") == 5

    def test_function_call_round(self):
        assert _safe_eval("round(3.7)") == 4

    def test_function_call_min_max(self):
        assert _safe_eval("min(3, 1, 2)") == 1
        assert _safe_eval("max(3, 1, 2)") == 3

    def test_function_call_len(self):
        assert _safe_eval("len([1, 2, 3])") == 3

    def test_function_call_sum(self):
        assert _safe_eval("sum([1, 2, 3])") == 6

    def test_list_literal(self):
        assert _safe_eval("[1, 2, 3]") == [1, 2, 3]

    def test_tuple_literal(self):
        assert _safe_eval("(1, 2)") == (1, 2)

    def test_string_constant(self):
        assert _safe_eval("'hello'") == "hello"

    def test_syntax_error(self):
        with pytest.raises(ComputeExpressionError, match="syntax"):
            _safe_eval("def foo():")

    def test_disallowed_function(self):
        with pytest.raises(ComputeExpressionError, match="not allowed"):
            _safe_eval("eval('1')")

    def test_disallowed_name(self):
        with pytest.raises(ComputeExpressionError, match="not allowed"):
            _safe_eval("__import__")

    def test_dict_literal(self):
        assert _safe_eval('{"a": 1, "b": 2}') == {"a": 1, "b": 2}

    def test_dict_literal_empty(self):
        assert _safe_eval("{}") == {}

    def test_unsupported_node_type(self):
        with pytest.raises(ComputeExpressionError, match="Unsupported"):
            _safe_eval("{1, 2, 3}")

    def test_none_constant(self):
        assert _safe_eval("None") is None

    def test_bool_constant(self):
        assert _safe_eval("True") is True
        assert _safe_eval("False") is False

    def test_int_float_conversion(self):
        assert _safe_eval("int(3.9)") == 3
        assert _safe_eval("float(3)") == 3.0

    def test_str_conversion(self):
        assert _safe_eval("str(42)") == "42"

    def test_chained_comparison(self):
        assert _safe_eval("1 < 2 < 3") is True
        assert _safe_eval("1 < 2 > 3") is False

    def test_unsupported_constant_type(self):
        """Line 74: Unsupported constant type (e.g., complex numbers)."""
        with pytest.raises(ComputeExpressionError, match="Unsupported constant"):
            _safe_eval("1j")

    def test_unsupported_binary_op(self):
        """Line 78: e.g., bitwise operators."""
        with pytest.raises(ComputeExpressionError, match="Unsupported binary"):
            _safe_eval("3 & 5")

    def test_binop_evaluation_error(self):
        """Line 83-84: e.g., division by zero."""
        with pytest.raises(ComputeExpressionError, match="Evaluation error"):
            _safe_eval("1 / 0")

    def test_unsupported_unary_op(self):
        """Line 88: e.g., bitwise not."""
        with pytest.raises(ComputeExpressionError, match="Unsupported unary"):
            _safe_eval("~5")

    def test_unsupported_comparison_op(self):
        """Line 95: e.g., 'is' comparison."""
        with pytest.raises(ComputeExpressionError, match="Unsupported comparison"):
            _safe_eval("1 is 1")

    def test_non_name_function_call(self):
        """Line 110: e.g., method call or attribute access in function position."""
        with pytest.raises(ComputeExpressionError, match="Only simple function"):
            _safe_eval("(lambda x: x)(1)")

    def test_function_call_error(self):
        """Line 118-119: e.g., int('not_a_number')."""
        with pytest.raises(ComputeExpressionError, match="Function call error"):
            _safe_eval("int('abc')")

    def test_name_in_builtins_as_value(self):
        """Line 122: name reference to a builtin (not as a function call)."""
        result = _safe_eval("abs")
        assert result is abs

    def test_json_true_false_null(self):
        """JSON-style booleans and null from json.dumps interpolation."""
        assert _safe_eval("true") is True
        assert _safe_eval("false") is False
        assert _safe_eval("null") is None

    def test_len_of_list_of_dicts_json_style(self):
        """len() on array of objects with JSON booleans — the real use case."""
        expr = 'len([{"selectionType": "MATERIAL_TYPE", "isActive": true}])'
        assert _safe_eval(expr) == 1

    def test_len_of_list_of_dicts_python_style(self):
        """len() on array of objects with Python booleans (from str())."""
        expr = "len([{'selectionType': 'MATERIAL_TYPE', 'isActive': True}])"
        assert _safe_eval(expr) == 1

    def test_dict_with_json_booleans(self):
        assert _safe_eval('{"active": true, "deleted": false, "value": null}') == {
            "active": True, "deleted": False, "value": None,
        }
