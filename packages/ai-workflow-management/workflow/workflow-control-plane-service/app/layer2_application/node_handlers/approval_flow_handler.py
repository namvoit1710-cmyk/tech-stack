"""APPROVAL_FLOW node handler: fetches approval process from governance API and transforms to graph."""

from __future__ import annotations

import logging
from typing import Any

from app.layer1_domain.events import NodeApprovalFlow
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.node_spec.specs import ApprovalFlowConfig
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.inline_bases import (
    AsyncInlineHandlerBase,
)
from app.layer2_application.node_handlers.schema_providers import (
    ApprovalFlowSchemaProvider,
    SchemaProvider,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import VariableResolver
from app.layer2_application.interfaces.governance_client_interface import IGovernanceClient
from app.layer2_application.read.approval_process_transformer import ApprovalProcessTransformer

logger = logging.getLogger(__name__)


def _approval_flow_event(
    *,
    run: WorkflowRun,
    node_id: str,
    node_name: str,
    instruction: str,
    workflow_id: str,
    output_port: str,
    change_request_id: str = "",
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
    error: str = "",
) -> NodeApprovalFlow:
    return NodeApprovalFlow(
        run_id=run.id,
        workflow_id=workflow_id,
        node_id=node_id,
        node_name=node_name,
        instruction=instruction,
        change_request_id=change_request_id,
        nodes=list(nodes or []),
        edges=list(edges or []),
        output_port=output_port,
        error=error,
        root_run_id=run.root_run_id or run.id,
        depth=run.depth_level,
        source="workflow-control-plane",
    )


def _step_sort_key(step: dict[str, Any]) -> tuple:
    """Null-/type-safe sort key for approval steps (SA-1759).

    ``stepOrder`` may be missing, ``None``, an int, or a numeric string; the
    old key ``(x.get("stepOrder"), x.get("stepId"))`` raised ``TypeError``
    when it mixed ``None`` with ints. Steps with a usable numeric order sort
    first (by that order); the rest sort after, keyed by ``stepId`` string.
    Never raises on missing/mixed keys.
    """
    order = step.get("stepOrder")
    try:
        order_rank = (0, float(order))
    except (TypeError, ValueError):
        order_rank = (1, 0.0)
    step_id = step.get("stepId")
    return (order_rank, str(step_id) if step_id is not None else "")


class ApprovalFlowNodeHandler(AsyncInlineHandlerBase):
    """Handler for APPROVAL_FLOW node type.

    Fetches change request approval process data from the governance API,
    transforms the flat list into a graph structure (nodes + edges), and
    writes the result as node output.

    Completion mode: ``immediate`` — the governance API call is awaited
    inline and the result is written to ``state_updates`` in a single pass.
    """

    def __init__(
        self,
        variable_resolver: VariableResolver,
        governance_client: IGovernanceClient | None = None,
        transformer: ApprovalProcessTransformer | None = None,
    ) -> None:
        self._resolver = variable_resolver
        self._governance_client = governance_client
        self._transformer = transformer or ApprovalProcessTransformer()
        self._schema_provider = ApprovalFlowSchemaProvider()

    def get_schema_provider(self) -> SchemaProvider:
        # /schema returns the computed approval graph (nodes/edges).
        return self._schema_provider

    async def handle(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        completed_task: Task | None,
        task_factory: TaskFactory,
    ) -> NodeHandlerResult:
        result = NodeHandlerResult()
        result.output_port = "success"
        result.completion_mode = "immediate"

        # change_request_id and instruction are config (canonical keys,
        # NodeSpec Phase 3); the input_binding fallback died with the clean break.
        config = ApprovalFlowConfig.from_node(node)
        instruction = config.instruction
        cr_id = await self._resolver.resolve(str(config.change_request_id), run.context.variables)
        cr_id = str(cr_id).strip()

        if not cr_id:
            logger.warning("APPROVAL_FLOW node %s: no change_request_id provided", node.id)
            result.output_port = "failure"
            result.state_updates[node.id] = {
                "output": {"error": "change_request_id is required"},
                "metadata": {"type": "approval_flow"},
            }
            result.events.append(
                _approval_flow_event(
                    run=run,
                    workflow_id=workflow.id,
                    node_id=node.id,
                    node_name=node.name,
                    instruction=instruction,
                    output_port="failure",
                    error="change_request_id is required",
                )
            )
            return result

        if self._governance_client is None:
            logger.error("APPROVAL_FLOW node %s: governance client not configured", node.id)
            result.output_port = "failure"
            result.state_updates[node.id] = {
                "output": {"error": "Governance API client not configured"},
                "metadata": {"type": "approval_flow"},
            }
            result.events.append(
                _approval_flow_event(
                    run=run,
                    workflow_id=workflow.id,
                    node_id=node.id,
                    node_name=node.name,
                    instruction=instruction,
                    output_port="failure",
                    error="Governance API client not configured",
                )
            )
            return result

        # Fetch approval data from governance API
        try:
            approval_data = await self._governance_client.get_approval_process(cr_id)
        except Exception as exc:  # noqa: BLE001 - governance client may surface transport/runtime failures
            error_message = f"Governance API call failed: {exc}"
            logger.error(
                "APPROVAL_FLOW node %s: governance API call failed: %s",
                node.id, exc,
            )
            result.output_port = "failure"
            result.state_updates[node.id] = {
                "output": {"error": error_message},
                "metadata": {"type": "approval_flow", "change_request_id": cr_id},
            }
            result.events.append(
                _approval_flow_event(
                    run=run,
                    workflow_id=workflow.id,
                    node_id=node.id,
                    node_name=node.name,
                    instruction=instruction,
                    output_port="failure",
                    change_request_id=cr_id,
                    error=error_message,
                )
            )
            return result

        # Extract approval steps. The real governance client returns a bare
        # list of steps; tolerate a legacy enveloped {"processes": [...]} dict
        # too, and default to an empty list for any other / failed shape.
        if isinstance(approval_data, dict):
            approval_steps = approval_data.get("processes")
        else:
            approval_steps = approval_data
        if not isinstance(approval_steps, list):
            approval_steps = []

        # SA-1759: sorting + transforming can raise on malformed step data
        # (missing/mixed ``stepOrder`` → the old sort key crashed with
        # ``TypeError``; a non-dict step → ``AttributeError``). This block is
        # outside the governance-fetch try/except above, so any raise here
        # crashed the whole node instead of routing the ``failure`` port this
        # handler sets on every other error path. Route ``failure`` instead.
        try:
            steps = [s for s in approval_steps if isinstance(s, dict)]
            steps.sort(key=_step_sort_key)
            graph = self._transformer.transform(steps)
            graph_dict = graph.to_dict()
        except Exception as exc:  # noqa: BLE001 - malformed payload must not crash the node
            error_message = f"Malformed approval process data: {exc}"
            logger.error(
                "APPROVAL_FLOW node %s: failed to process approval steps: %s",
                node.id, exc,
            )
            result.output_port = "failure"
            result.state_updates[node.id] = {
                "output": {"error": error_message},
                "metadata": {"type": "approval_flow", "change_request_id": cr_id},
            }
            result.events.append(
                _approval_flow_event(
                    run=run,
                    workflow_id=workflow.id,
                    node_id=node.id,
                    node_name=node.name,
                    instruction=instruction,
                    output_port="failure",
                    change_request_id=cr_id,
                    error=error_message,
                )
            )
            return result

        result.state_updates[node.id] = {
            "output": {
                "nodes": graph_dict["nodes"],
                "edges": graph_dict["edges"],
                "change_request_id": cr_id,
            },
            "metadata": {
                "type": "approval_flow",
                "change_request_id": cr_id,
                "step_count": len(steps),
            },
        }
        result.events.append(
            _approval_flow_event(
                run=run,
                workflow_id=workflow.id,
                node_id=node.id,
                node_name=node.name,
                instruction=instruction,
                output_port="success",
                change_request_id=cr_id,
                nodes=graph_dict["nodes"],
                edges=graph_dict["edges"],
            )
        )
        return result

