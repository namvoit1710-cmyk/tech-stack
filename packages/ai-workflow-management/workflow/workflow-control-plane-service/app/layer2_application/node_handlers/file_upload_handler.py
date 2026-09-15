"""FILE_UPLOAD node handler: pauses workflow to allow a user to upload a file."""

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
from app.layer1_domain.node_spec.specs import FileUploadConfig
from app.layer2_application.node_handlers.pending_base import PendingHandlerBase
from app.layer2_application.resolve.variable_resolver import VariableResolver
from app.layer1_domain.auto_form_mapper import single_upload_field


class FileUploadNodeHandler(PendingHandlerBase):
    """Pauses the run for a single-file upload.

    The FE-facing schema is a one-field ``single_upload_field``; the
    provisioner sees a synthetic ``string`` schema for the same key so
    the root CSV is created and PATCH /data can write the uploaded
    file's metadata into it.
    """

    pending_kind: ClassVar[str] = "file_upload"

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
        return [
            single_upload_field(
                key="file",
                label="Upload File",
            ).to_dict()
        ]

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
        # Provisioner needs a ``string`` outputType to include the
        # ``file`` field in the root CSV columns. The FE-facing
        # ``single_upload_field`` has ``outputType=object`` which
        # ``root_scalar_columns`` skips, so we hand it a one-column
        # synthetic schema for the same ``file`` key.
        provisioning_schema = [
            {"key": "file", "outputType": "string"},
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
            "node_type": "FILE_UPLOAD",
            "input_schema": input_schema,
            "instruction": ctx.get("instruction", ""),
            "prefilled": prefilled,
            "artifact_key": FileUploadConfig.from_node(node).artifact_key,
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
            pending_kind="file_upload",
            parent_run_id=run.parent_run_id or "",
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level,
            source="workflow-control-plane",
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
            pending_kind="file_upload",
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level,
            source="workflow-control-plane",
        )
        return EventTopic.INPUT_SUBMITTED, payload
