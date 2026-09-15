"""INPUT node handler: pauses workflow to collect structured input from a user."""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from app.layer1_domain.events import (
    DomainEvent,
    EventTopic,
    InputRequested,
    InputSubmitted,
)
from app.layer1_domain.exceptions import ExpressionError
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.node_spec.specs import InputNodeConfig
from app.layer2_application.node_handlers.pending_base import PendingHandlerBase
from app.layer2_application.resolve.variable_resolver import (
    VariableResolver,
    VariableResolutionError,
)
from app.layer1_domain.auto_form_mapper.helpers import table_array_field, text_field

logger = logging.getLogger(__name__)

# Maps UI-specific field types to coercion types supported by VariableResolver.
_TYPE_COERCION_MAP: dict[str, str] = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
    "object": "object",
    "array": "array",
    "textarea": "string",
    "select": "string",
    "date": "string",
    "file": "string",
}


def _parse_input_schema(raw: Any) -> list:
    """Parse input_schema from JSON string or list."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


class InputNodeHandler(PendingHandlerBase):
    """Collects structured input from a user.

    Schema source: design-time ``config['input_schema']`` plus an
    optional dynamic-table mode that synthesises a table_array_field
    schema from the headers of a bound file.

    Override surface: this handler customises ``resolve_prefill`` to
    materialise file refs in the resolution context (so prefill
    expressions referencing a CSV node resolve to actual values), and
    customises ``build_input_schema`` to honour the dynamic-table
    mode and to resolve ``{{...}}`` expressions inside schema
    defaults.
    """

    pending_kind: ClassVar[str] = "input"

    def __init__(
        self,
        variable_resolver: VariableResolver,
        data_store_service: Any = None,
        schema_transform_client: Any = None,
        file_service_client: Any = None,
    ) -> None:
        super().__init__(variable_resolver)
        self._data_store = data_store_service
        self._schema_transform = schema_transform_client
        self._file_client = file_service_client

    # ------------------------------------------------------------------
    # PendingHandlerBase hooks
    # ------------------------------------------------------------------

    async def resolve_prefill(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        # Materialise file refs so expressions like {{$node1.output.name}}
        # resolve to actual values even when node1's output is a CSV ref.
        resolve_ctx = await self._build_resolve_context(run.context.variables)
        ctx["resolve_ctx"] = resolve_ctx

        if not node.input_binding:
            return {}

        # Prefer explicit input_types; fall back to schema-derived map
        # (computed in build_input_schema, so we re-derive here when
        # needed).
        cfg = InputNodeConfig.from_node(node)
        type_map = cfg.input_types
        if not type_map:
            input_schema = _parse_input_schema(cfg.input_schema or [])
            if input_schema:
                type_map = self._extract_type_map(input_schema)

        try:
            return await self._resolver.resolve_mapping(
                node.input_binding, resolve_ctx, type_map=type_map,
            )
        except VariableResolutionError as exc:
            raise ExpressionError(
                f"Node '{node.id}': input resolution failed: {exc}"
            ) from exc

    async def build_input_schema(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        prefilled: dict[str, Any],
        ctx: dict[str, Any],
    ) -> list:
        cfg = InputNodeConfig.from_node(node)
        input_schema = _parse_input_schema(cfg.input_schema or [])

        # Dynamic table mode: synthesize a table_array_field schema
        # from file headers when ``config.file_id`` is set and the user
        # opted into dynamic_table (or never declared a static schema).
        if cfg.file_id and (cfg.dynamic_table or not input_schema):
            dynamic_schema = await self._build_dynamic_table_schema(
                node, ctx.get("resolve_ctx", run.context.variables),
            )
            if dynamic_schema is not None:
                input_schema = dynamic_schema

        # Resolve ``{{...}}`` expressions inside schema defaults so the
        # FE receives concrete prefill values rather than raw templates.
        resolved_schema = await self._resolve_schema_expressions(
            input_schema, ctx.get("resolve_ctx", run.context.variables),
        )
        return resolved_schema

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
            provision_schema_artifacts,
        )
        existing = run.context.variables.get(node.id) if run.context else None
        # ``force_root`` so even a scalar-less / table-less INPUT schema still
        # gets a header-only ``__row_id`` root — INPUT has no synthetic column
        # to inject (unlike HUMAN_ACTION's ``_action`` / FILE_UPLOAD's
        # ``file``), so this is how it upholds the "every input-emitting node
        # has a non-empty ``__root__.file_id``" invariant.
        return await provision_schema_artifacts(
            input_schema=input_schema,
            existing_ref=existing,
            file_client=self._file_client,
            node_id=node.id,
            force_root=True,
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
            "node_type": "INPUT",
            "input_schema": input_schema,
            "instruction": ctx.get("instruction", ""),
            "prefilled": prefilled,
            "timeout_minutes": InputNodeConfig.from_node(node).timeout_minutes,
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
            pending_kind="input",
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
            pending_kind="input",
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level,
            source="workflow-control-plane",
        )
        return EventTopic.INPUT_SUBMITTED, payload

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _build_resolve_context(self, variables: dict[str, Any]) -> dict[str, Any]:
        """Return variables as-is. File-backed lookups happen lazily through
        ``FileBackedResolverStrategy`` in the resolver chain — eager
        materialization here would download every file_ref variable on
        every prefill, even ones the input_binding never references.
        Empty-path expressions (``{{$node}}``) still materialize on
        demand via the strategy's own empty-path branch."""
        return variables

    async def _build_dynamic_table_schema(
        self,
        node: WorkflowNode,
        context: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        """Build a table_array_field schema dynamically from file headers + data.

        Returns None if dynamic generation is not applicable or fails.
        """
        if not self._schema_transform or not self._file_client:
            return None

        raw_file_id = InputNodeConfig.from_node(node).file_id
        if not raw_file_id:
            return None

        file_id = raw_file_id
        if isinstance(raw_file_id, str) and "{{" in raw_file_id:
            try:
                file_id = await self._resolver.resolve(raw_file_id, context)
            except VariableResolutionError:
                return None

        if not file_id:
            return None

        try:
            headers = await self._schema_transform.extract_headers(file_id)
        except Exception:
            logger.warning("extract_headers failed for file_id=%s", file_id)
            return None
        if not headers:
            return None

        columns = []
        for hdr in headers:
            col_name = hdr.get("name", "")
            if not col_name:
                continue
            columns.append(text_field(key=col_name, label=col_name))

        if not columns:
            return None

        # Row data is intentionally not embedded in the schema — the FE
        # fetches rows directly from the file-service via OData. Keeping
        # the schema row-free avoids loading large CSVs into control-plane
        # memory.
        table = table_array_field(
            key="table_data",
            label=InputNodeConfig.from_node(node).instruction or node.name or "Data Table",
            columns=columns,
        )
        return [table.to_dict()]

    @staticmethod
    def _extract_type_map(input_schema: list) -> dict[str, str]:
        """Extract a field-key -> coercion-type mapping from input_schema."""
        type_map: dict[str, str] = {}
        if not isinstance(input_schema, list):
            return type_map
        for field_def in input_schema:
            if not isinstance(field_def, dict):
                continue
            key = field_def.get("key")
            if not key:
                continue
            raw_type = field_def.get("type") or field_def.get("outputType") or "string"
            type_map[key] = _TYPE_COERCION_MAP.get(raw_type, raw_type)
        return type_map
