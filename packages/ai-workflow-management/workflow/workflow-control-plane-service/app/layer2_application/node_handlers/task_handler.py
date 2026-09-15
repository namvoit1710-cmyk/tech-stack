"""TASK node handler: resolve input mappings, dispatch to worker."""

from __future__ import annotations

import asyncio

from datetime import datetime, timezone
from typing import Any

from app.layer1_domain.exceptions import (
    CredentialNotFound,
    CredentialResolveAuthError,
    ExpressionError,
    InputRequirementError,
)
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer2_application.credentials import (
    RunCredentialProvider,
    credential_ref_of,
)
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.dispatching_base import (
    DispatchingHandlerBase,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import VariableResolver, VariableResolutionError
from app.layer1_domain.value_objects.agent_ref import AgentRef


class TaskNodeHandler(DispatchingHandlerBase):
    def __init__(
        self,
        variable_resolver: VariableResolver,
        worker_registry_client: Any = None,
        credential_cipher: Any = None,
        credential_provider: Any = None,
    ) -> None:
        self._resolver = variable_resolver
        self._worker_client = worker_registry_client
        self._credential_cipher = credential_cipher
        # SA-2287 (B3): this node's credential is fetched HERE, at dispatch,
        # not sealed into the run at start. Falling back to a cipher-only
        # provider keeps every caller that passes just ``credential_cipher``
        # working — that provider serves the run's sealed map and never
        # reaches the vault, which is exactly what those callers had before.
        self._credential_provider = credential_provider or RunCredentialProvider(
            cipher=credential_cipher,
        )

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

        # Phase 6 — uniform execution fields.
        result.completion_mode = "dispatched"

        resolved_input: dict[str, Any] = {}
        if node.input_binding:
            try:
                type_map = node.config.get("input_types") or self._build_type_map_from_schema(node)
                resolved_input = await self._resolver.resolve_mapping(
                    node.input_binding, run.context.variables, type_map=type_map,
                    coerce_strict=False,
                )
            except VariableResolutionError as exc:
                raise ExpressionError(
                    f"Node '{node.id}': input resolution failed: {exc}"
                ) from exc
        if node.input_require:
            missing = [k for k in node.input_require if k not in resolved_input or resolved_input[k] is None]
            if missing:
                raise InputRequirementError(
                    f"Node '{node.id}': missing required input keys: {missing}. "
                    f"Resolved keys: {sorted(resolved_input.keys())}"
                )
        resolved_input = await self._apply_node_definition(node, resolved_input, run)
        resolved_input = await self._resolve_function_calls(node, resolved_input, run)

        # Resolve the instruction field using VariableResolver.
        # The resolved instruction is the authoritative brief/prompt for the agent.
        resolved_instruction = await self._resolve_instruction(node, run)
        if resolved_instruction:
            resolved_input["_instruction"] = resolved_instruction

        if node.action:
            resolved_input["_action"] = node.action
        if node.agents:
            resolved_input["_agents"] = self._serialize_agents(node.agents)

        # THE EGRESS BOUNDARY (SA-2287 / B3). Fetch this node's credential now,
        # from the vault, and hand it straight to the worker. Nothing was
        # resolved at StartRun, so this is the only place the secret exists —
        # and only for the node about to use it.
        #
        # The shape is unchanged on purpose: ``worker_sdk...effective_auth.
        # borrow_credential`` reads ``inputs["__credentials"][credential_ref]``
        # (falling back to the literal ``"default"``), so it must stay a
        # ``{ref: {type, config}}`` dict. The map is redacted from NodeInput
        # events by the engine.
        credentials = await self._credentials_for(node, run)
        if credentials:
            resolved_input["__credentials"] = credentials

        if not node.worker_type:
            raise ExpressionError(
                f"TASK node '{node.id}' has no worker_type configured"
            )
        task = task_factory.create_task(node, run, resolved_input=resolved_input)
        task.started_at = datetime.now(timezone.utc).isoformat()
        result.tasks_to_dispatch.append(task)

        # Phase 6 — pass resolved input + worker type so the uniform
        # engine path (_exec_uniform "dispatched" branch) can dispatch
        # without re-running the handler.
        result.resolved_input = resolved_input
        result.worker_type = node.worker_type or ""

        # NOTE: NodeDispatched event is NOT emitted here — it is published by
        # OrchestrationEngine._exec_uniform() with the full payload
        # (input, node_config, idempotency_key, correlation_id).  Emitting it
        # here as well caused every task to be dispatched twice.
        return result

    async def _credentials_for(
        self, node: WorkflowNode, run: WorkflowRun,
    ) -> dict[str, Any]:
        """This node's credential map, fetched at dispatch.

        A vault VERDICT (not found / refused) becomes an ``ExpressionError``,
        which ``_exec_uniform`` already routes to ``NodeDispatched(
        dispatch_to_worker=False)`` + ``NodeFailed``: the node goes red with
        this message and the empty task is never forwarded to a worker that
        would overwrite the error with its own 401. That is the whole reason a
        refusal is worth converting rather than letting escape — and it is
        strictly better than where it used to land, because before B3 the same
        refusal happened at StartRun and left no run at all (SA-2294).

        Any OTHER exception (timeout, connection reset) propagates untouched so
        the existing retry paths still treat it as transient.
        """
        try:
            return await self._credential_provider.for_node(node, run)
        except (CredentialNotFound, CredentialResolveAuthError) as exc:
            ref = credential_ref_of(node)
            # ASCII only: this text is formatted while handling an exception,
            # then logged and persisted. A UnicodeEncodeError raised here would
            # replace the diagnostic with a crash.
            raise ExpressionError(
                f"TASK node '{node.id}': the vault refused credential {ref!r} "
                f"for tenant '{run.tenant_id}' at dispatch: "
                f"{type(exc).__name__}: {exc}. If this run has no user behind "
                f"it (event or schedule trigger), a user-owned credential "
                f"resolves only when its owner turns on Automation for it "
                f"(SA-2279)."
            ) from exc

    async def _resolve_instruction(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
    ) -> str:
        """Resolve the node's instruction field using VariableResolver.

        SA-517: The instruction field supports {{$node.field}} variable syntax.
        This method resolves those variables against the run context and returns
        the authoritative prompt string for the agent.

        Returns:
            The resolved instruction string, or empty string if not set.

        Raises:
            ExpressionError: If variable resolution fails.
        """
        instruction = node.instruction
        if not instruction:
            return ""
        try:
            resolved = await self._resolver.resolve(instruction, run.context.variables)
            return str(resolved) if resolved else ""
        except VariableResolutionError as exc:
            raise ExpressionError(
                f"Node '{node.id}': instruction resolution failed: {exc}"
            ) from exc

    @staticmethod
    def _serialize_agents(agents: list[AgentRef]) -> list[dict[str, Any]]:
        """Convert list of AgentRef to list of dicts for task payload."""
        return [
            a.to_dict() if isinstance(a, AgentRef) else a
            for a in agents
        ]

    async def _apply_node_definition(
        self,
        node: WorkflowNode,
        resolved_input: dict[str, Any],
        run: WorkflowRun,
    ) -> dict[str, Any]:
        """Merge locked defaults and apply input mapping from a snapshotted node definition."""
        snapshot = node.definition_snapshot
        if not snapshot:
            return resolved_input
        locked_defaults = snapshot.get("locked_defaults") or {}
        input_mapping = snapshot.get("input_mapping") or {}
        if input_mapping:
            mapping_context = {**run.context.variables, "custom": resolved_input}
            try:
                mapped = await self._resolver.resolve_mapping(input_mapping, mapping_context)
            except VariableResolutionError:
                mapped = {}
            result = {**resolved_input, **mapped, **locked_defaults}
        else:
            result = {**resolved_input, **locked_defaults}

        # Auto-coerce types from input_schema outputType declarations
        type_map = self._build_type_map_from_schema(node)
        if type_map:
            for key, target_type in type_map.items():
                if key in result and result[key] is not None:
                    try:
                        result[key] = self._resolver.coerce_value(result[key], target_type)
                    except VariableResolutionError:
                        pass  # keep original value if coercion fails
        return result

    @staticmethod
    def _build_type_map_from_schema(node: WorkflowNode) -> dict[str, str]:
        """Build a {field_key: outputType} map from the node definition's input_schema."""
        snapshot = node.definition_snapshot or {}
        input_schema = snapshot.get("input_schema", [])
        if not input_schema:
            return {}
        type_map: dict[str, str] = {}
        for field in input_schema:
            key = field.get("key", "")
            output_type = field.get("outputType", "")
            if key and output_type:
                type_map[key] = output_type
        return type_map

    async def _resolve_function_calls(
        self,
        node: WorkflowNode,
        resolved_input: dict[str, Any],
        run: WorkflowRun,
    ) -> dict[str, Any]:
        """Scan resolved_input for _function_call markers and invoke worker functions."""
        if not self._worker_client:
            return resolved_input
        worker_type = node.worker_type
        if not worker_type:
            return resolved_input

        result = dict(resolved_input)
        for key, value in list(result.items()):
            if not isinstance(value, dict) or "_function_call" not in value:
                continue
            spec = value["_function_call"]
            func_name = spec.get("worker_function", "")
            raw_params = spec.get("params", {})
            # Resolve expression params against context + current input
            if raw_params and self._resolver:
                try:
                    context = {**run.context.variables, "custom": resolved_input}
                    resolved_params = await self._resolver.resolve_mapping(raw_params, context)
                except VariableResolutionError:
                    resolved_params = raw_params
            else:
                resolved_params = raw_params
            try:
                func_result = await asyncio.to_thread(
                    self._worker_client.invoke_worker_function,
                    worker_type, func_name, resolved_params,
                )
                if isinstance(func_result, dict) and func_result.get("status") == "success":
                    result[key] = func_result.get("result", {})
                else:
                    result[key] = {"_function_call_error": func_result.get("error", "Unknown error")}
            except Exception as exc:
                result[key] = {"_function_call_error": str(exc)}
        return result
