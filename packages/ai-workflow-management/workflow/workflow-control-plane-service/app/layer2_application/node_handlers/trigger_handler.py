"""TRIGGER node handler: entry point for workflow execution."""

from __future__ import annotations

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.node_spec.specs import TriggerConfig
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.inline_bases import (
    PureInlineHandlerBase,
)
from app.layer2_application.orchestration.task_factory import TaskFactory


class TriggerNodeHandler(PureInlineHandlerBase):
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
        result.output_port = "out_default"
        result.completion_mode = "immediate"

        trigger_data = {
            "type": TriggerConfig.from_node(node).trigger_type,
            "body": run.input_data,
        }
        result.state_updates[node.id] = {
            "output": trigger_data,
            "metadata": {"type": "trigger", "trigger_type": trigger_data["type"]},
        }
        result.state_updates["trigger"] = trigger_data
        return result
