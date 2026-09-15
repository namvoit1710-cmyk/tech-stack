"""``PendingHandlerBase`` — template method for nodes that pause the run.

The four PENDING handlers (INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD)
share the same skeleton:

1. Resolve any prefill values from ``node.input_binding``.
2. Build the runtime ``input_schema`` exposed to the FE.
3. Optionally pre-provision an empty file-ref tree so PATCH /data has
   a target on first edit.
4. Create a PENDING task and stamp its ``output_metadata``.
5. Emit an ``InputRequested`` event with the subclass's
   ``pending_kind`` discriminator.

This base owns the skeleton; subclasses implement the four ``build_*``
hooks plus optional ``provision_artifacts`` / ``resolve_prefill``
overrides. Per-call intermediate state (e.g. DATA_EDIT's resolved
file_id and source-info) lives in a ``ctx`` dict threaded through the
hooks — a singleton handler must not store request state on ``self``.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, ClassVar, Optional

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.events import DomainEvent
from app.layer1_domain.exceptions import ExpressionError
from app.layer2_application.node_handlers.base import (
    NodeHandlerResult,
    NodeHandlerShape,
)
from app.layer2_application.node_handlers.base_node_handler import BaseNodeHandler
from app.layer2_application.node_handlers.schema_providers import (
    SchemaProvider,
    StaticSchemaProvider,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    VariableResolutionError,
    VariableResolver,
)
from app.layer1_domain.value_objects.task_status import TaskStatus


class PendingHandlerBase(BaseNodeHandler):
    """Template method for handlers that pause the run for user input.

    Subclass contract:

    * Set ``pending_kind`` (ClassVar str: ``"input"``, ``"human_action"``,
      ``"data_edit"``, ``"file_upload"``).
    * Implement :meth:`build_input_schema`, :meth:`build_output_metadata`,
      :meth:`build_input_requested_event`, :meth:`build_completion_event`.
    * Optionally override :meth:`resolve_prefill` (default resolves
      ``node.input_binding`` via the variable resolver) or
      :meth:`provision_artifacts` (default returns ``None`` — no file
      provisioning).
    * Override ``self._schema_provider`` in ``__init__`` if the node
      needs a non-default provider (e.g. DATA_EDIT swaps to
      :class:`DataEditSchemaProvider`).

    The base ``handle`` orchestrates the steps in order and owns the
    ``NodeHandlerResult`` shape (``completion_mode = "pending"``, task
    added to ``tasks_to_dispatch``, etc.).
    """

    shape: ClassVar[NodeHandlerShape] = NodeHandlerShape.PENDING_USER
    pending_kind: ClassVar[str] = ""

    def __init__(self, variable_resolver: VariableResolver) -> None:
        self._resolver = variable_resolver
        # Subclasses with bespoke schema resolution rebind this in their
        # own ``__init__`` (e.g. DataEditNodeHandler → DataEditSchemaProvider).
        # The provider is handed the *same* resolver this handler uses so the
        # design-time ``GET /schema`` payload resolves ``{{…}}`` identically to
        # the schema this handler stamps at dispatch.
        self._schema_provider: SchemaProvider = StaticSchemaProvider(variable_resolver)

    # ------------------------------------------------------------------
    # Schema provider hook (BaseNodeHandler override)
    # ------------------------------------------------------------------

    def get_schema_provider(self) -> SchemaProvider:
        return self._schema_provider

    # ------------------------------------------------------------------
    # Subclass extension points
    # ------------------------------------------------------------------

    @abstractmethod
    async def build_input_schema(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        prefilled: dict[str, Any],
        ctx: dict[str, Any],
    ) -> list:
        """Return the auto-form schema written to ``output_metadata``.

        The ``ctx`` dict is the per-call scratchpad shared between this
        method, :meth:`build_output_metadata`, and
        :meth:`build_input_requested_event` so subclasses can compute
        derived values (file_id, source_node_id, ...) exactly once.
        """
        ...

    @abstractmethod
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
        """Return the full ``task.output_metadata`` payload.

        Each subclass owns the keys it needs (``node_type``,
        ``instruction``, ``input_schema``, ``prefilled``, plus any
        node-type-specific extras like DATA_EDIT's ``source_file_id``).
        """
        ...

    @abstractmethod
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
        """Return the subclass-specific ``InputRequested`` event."""
        ...

    @abstractmethod
    def build_completion_event(
        self,
        task: Task,
        run: WorkflowRun,
        node: WorkflowNode,
        action: str,
    ) -> tuple[str, DomainEvent]:
        """Return ``(secondary_topic, InputSubmitted)`` for the resumed task.

        Same contract as the deleted ``PendingNodeHandler`` Protocol —
        ``emit_pending_node_completed`` in the engine still calls this
        from the registered handler.
        """
        ...

    # ------------------------------------------------------------------
    # Optional hooks
    # ------------------------------------------------------------------

    async def resolve_prefill(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve ``node.input_binding`` into a prefill dict.

        Default applies ``node.config['input_types']`` as the type_map
        and surfaces a clean :class:`ExpressionError` on resolution
        failure. DATA_EDIT overrides this to flatten the FE's
        TabWrapper nesting and to recover ``$artifacts.<name>``
        references when the global namespace is empty.
        """
        if not node.input_binding:
            return {}
        try:
            type_map = (node.config or {}).get("input_types")
            return await self._resolver.resolve_mapping(
                node.input_binding, run.context.variables, type_map=type_map
            )
        except VariableResolutionError as exc:
            raise ExpressionError(
                f"Node '{node.id}': input resolution failed: {exc}"
            ) from exc

    async def provision_artifacts(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        input_schema: list,
        prefilled: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[Any]:
        """Pre-provision empty file_refs so PATCH /data has a target.

        Default returns ``None``. INPUT, HUMAN_ACTION, FILE_UPLOAD
        delegate to ``provision_schema_artifacts``; DATA_EDIT overrides
        with its own ``working_ref`` minting.
        """
        return None

    async def resolve_instruction(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        prefilled: dict[str, Any],
        ctx: dict[str, Any],
    ) -> str:
        """Return the user-facing instruction string, with ``{{…}}`` resolved.

        Default reads ``node.config['instruction']`` then falls back to
        ``node.instruction`` and resolves any embedded expressions
        against the run context (same contract as TASK nodes). DATA_EDIT
        overrides to prefer the prefilled value (the palette form's
        already-resolved Instruction field).
        """
        cfg = node.config or {}
        raw = cfg.get("instruction", getattr(node, "instruction", "") or "")
        return await self._resolve_instruction_text(raw, run, ctx)

    async def _resolve_instruction_text(
        self,
        raw: Any,
        run: WorkflowRun,
        ctx: dict[str, Any],
    ) -> str:
        """Resolve ``{{…}}`` expressions in an instruction string.

        Resolves against ``ctx['resolve_ctx']`` when a handler has
        materialised one (INPUT), else the raw run variables. A string
        whose expression fails to resolve is returned unchanged; a
        full-match expression resolving to a non-string is stringified
        since the instruction is rendered as text.
        """
        if not isinstance(raw, str) or "{{" not in raw:
            return raw or ""
        context = ctx.get("resolve_ctx", run.context.variables)
        try:
            resolved = await self._resolver.resolve(raw, context)
        except VariableResolutionError:
            return raw
        return resolved if isinstance(resolved, str) else str(resolved)

    # ------------------------------------------------------------------
    # Shared schema-expression resolution
    # ------------------------------------------------------------------

    async def _resolve_schema_expressions(
        self,
        schema: list,
        context: dict[str, Any],
    ) -> list:
        """Resolve ``{{…}}`` expressions anywhere in an FE-facing schema.

        Thin wrapper over :meth:`VariableResolver.resolve_schema` — kept so
        the INPUT/HUMAN_ACTION call sites stay unchanged. Delegating to the
        resolver (instead of a private copy) is what guarantees the
        dispatch-time schema and the ``GET /schema`` design-time schema
        resolve through exactly the same code. A non-list ``schema`` is
        returned unchanged, preserving the historical contract.
        """
        if not isinstance(schema, list):
            return schema
        return await self._resolver.resolve_schema(schema, context)

    # ------------------------------------------------------------------
    # Template method
    # ------------------------------------------------------------------

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
        result.completion_mode = "pending"

        ctx: dict[str, Any] = {}

        prefilled = await self.resolve_prefill(node=node, run=run, ctx=ctx)
        input_schema = await self.build_input_schema(
            node=node, run=run, workflow=workflow,
            prefilled=prefilled, ctx=ctx,
        )

        provisioned_ref = await self.provision_artifacts(
            node=node, run=run,
            input_schema=input_schema, prefilled=prefilled, ctx=ctx,
        )
        if provisioned_ref is not None:
            result.state_updates[node.id] = provisioned_ref

        task_input: Any = (
            provisioned_ref if provisioned_ref is not None else prefilled
        )
        task = task_factory.create_task(node, run, resolved_input=task_input)
        task.status = TaskStatus.PENDING

        # Resolve the instruction once, before output_metadata, so both the
        # stamped metadata and the InputRequested event carry the resolved
        # text. ``ctx['instruction']`` is the single source the subclasses'
        # ``build_output_metadata`` reads.
        instruction = await self.resolve_instruction(
            node=node, run=run, prefilled=prefilled, ctx=ctx,
        )
        ctx["instruction"] = instruction

        task.output_metadata = self.build_output_metadata(
            node=node, run=run,
            resolved_input=task_input,
            prefilled=prefilled, input_schema=input_schema, ctx=ctx,
        )

        result.pending_task = task
        result.tasks_to_dispatch.append(task)

        result.events.append(self.build_input_requested_event(
            task=task, run=run, node=node, instruction=instruction,
            prefilled=prefilled, input_schema=input_schema, ctx=ctx,
        ))

        # Run-through (the INPUT schema's ``runThrough: true``): auto-submit
        # the resolved prefill without pausing the run. The InputRequested
        # event above still reaches the wire (FE renders it transiently /
        # for audit), but the node completes immediately with the prefilled
        # values as its output — the same end state as an instant user
        # submit of the defaults.
        if node.config.get("runThrough"):
            from datetime import datetime, timezone

            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc).isoformat()
            task.output_data = dict(prefilled or {})
            result.pending_task = None
            result.completion_mode = "immediate"
            result.state_updates[node.id] = {
                "output": dict(prefilled or {}),
                "metadata": {"type": self.pending_kind, "run_through": True},
            }
        return result


__all__ = ["PendingHandlerBase"]
