"""HUMAN_ACTION node handler: pauses workflow for a human to confirm or reject."""

from __future__ import annotations

from typing import Any, ClassVar

from app.layer1_domain.events import (
    DomainEvent,
    EventTopic,
    InputRequested,
    InputSubmitted,
)
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.node_spec.specs import HumanActionConfig
from app.layer2_application.node_handlers.pending_base import PendingHandlerBase
from app.layer2_application.resolve.variable_resolver import VariableResolver


class HumanActionNodeHandler(PendingHandlerBase):
    """Pauses the run for a human to confirm or reject an action.

    Provisioning trick: the FE-facing schema in ``output_metadata``
    keeps only the user-defined fields, but the provisioner needs a
    synthetic ``_action`` column so the root CSV is created even when
    the node defines no extra fields. The Confirm/Reject buttons drive
    that column directly.
    """

    pending_kind: ClassVar[str] = "human_action"

    def __init__(
        self,
        variable_resolver: VariableResolver,
        file_service_client: Any = None,
        data_store_service: Any = None,
    ) -> None:
        super().__init__(variable_resolver)
        self._file_client = file_service_client
        self._data_store = data_store_service

    # ------------------------------------------------------------------
    # PendingHandlerBase hooks
    # ------------------------------------------------------------------

    async def build_input_schema(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        prefilled: dict[str, Any],
        ctx: dict[str, Any],
    ) -> list:
        user_schema = list(HumanActionConfig.from_node(node).input_schema or ())
        if not isinstance(user_schema, list):
            user_schema = []
        # Resolve ``{{…}}`` expressions inside the user-authored schema
        # (field defaults, labels, …) so the FE receives concrete prefill
        # values rather than raw templates — same contract as INPUT.
        return await self._resolve_schema_expressions(
            user_schema, run.context.variables,
        )

    async def provision_artifacts(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        input_schema: list,
        prefilled: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Any | None:
        if self._file_client is None:
            return None
        from app.layer2_application.data.schema_artifact_provisioner import (
            node_schema_declares_artifacts,
            provision_schema_artifacts,
        )
        # Always include ``_action`` so the root CSV is created even
        # when the user defined no extra form fields.
        provisioning_schema = [
            {"key": "_action", "outputType": "string"},
            *input_schema,
        ]
        if not node_schema_declares_artifacts(provisioning_schema):
            return None
        existing = run.context.variables.get(node.id) if run.context else None
        return await provision_schema_artifacts(
            input_schema=provisioning_schema,
            existing_ref=existing,
            file_client=self._file_client,
            node_id=node.id,
        )

    def build_output_metadata(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        resolved_input: Any,
        prefilled: dict[str, Any],
        input_schema: list,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "node_type": "HUMAN_ACTION",
            "instruction": ctx.get("instruction", ""),
            "input_schema": input_schema,
            "confirm_label": HumanActionConfig.from_node(node).confirm_label,
            "reject_label": HumanActionConfig.from_node(node).reject_label,
            "prefilled": prefilled,
        }

    def build_input_requested_event(
        self,
        *,
        task: Task,
        run: WorkflowRun,
        node: WorkflowNode,
        instruction: str,
        prefilled: dict[str, Any],
        input_schema: list,
        ctx: dict[str, Any],
    ) -> DomainEvent:
        return InputRequested(
            task_id=task.id,
            run_id=run.id,
            workflow_id=run.workflow_id,
            node_id=node.id,
            instruction=instruction,
            pending_kind="human_action",
            confirm_label=HumanActionConfig.from_node(node).confirm_label,
            reject_label=HumanActionConfig.from_node(node).reject_label,
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level,
            source="workflow-control-plane",
            parent_run_id=run.parent_run_id or "",
        )

    def build_completion_event(
        self,
        task: Task,
        run: WorkflowRun,
        node: WorkflowNode,
        action: str,
    ) -> tuple[str, DomainEvent]:
        payload = InputSubmitted(
            task_id=task.id,
            run_id=task.run_id,
            workflow_id=run.workflow_id,
            node_id=node.id,
            pending_kind="human_action",
            action=action,
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level,
            source="workflow-control-plane",
        )
        return EventTopic.INPUT_SUBMITTED, payload
