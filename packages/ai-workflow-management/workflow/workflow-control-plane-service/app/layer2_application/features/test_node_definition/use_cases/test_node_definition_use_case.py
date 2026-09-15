"""Test a node definition.

Two execution paths, selected by the definition's ``base_type``:

* **TASK / worker** — invoke the worker synchronously with the user input.
* **ENGINE builtin** (CONDITION/SWITCH/COMPUTE/SET_VARIABLE/MERGE) — evaluate the
  node's handler inline against the user input via :class:`StandaloneNodeRunner`.

PENDING types (INPUT/HUMAN_ACTION/DATA_EDIT/FILE_UPLOAD) and deferring ENGINE
types (LOOP/WORKFLOW/PARALLEL) cannot be tested in isolation — they need the
workflow run/topology context — and return ``NODE_NOT_TESTABLE_STANDALONE``
(HTTP 422) pointing the user at the canvas test endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.node_definition_repository_interface import INodeDefinitionRepository
from app.layer2_application.errors import ErrorCode
from app.layer2_application.orchestration.standalone_node_runner import (
    NotTestableStandalone,
    StandaloneNodeRunner,
)
from app.layer1_domain.node_spec.model import NodeRuntime
from app.layer1_domain.node_spec.registry import node_types_for_runtime
from app.layer1_domain.value_objects.node_type import NodeType


@dataclass
class TestNodeDefinitionCommand:
    __test__ = False
    node_definition_id: str = ""
    input_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TestNodeDefinitionResult:
    node_definition_id: str = ""
    node_type: str = ""
    worker_type: str = ""
    resolved_input: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    handler_output: dict[str, Any] = field(default_factory=dict)
    branch: str = ""
    output_reference: str = ""
    execution_time_ms: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class TestNodeDefinitionUseCase:
    __test__ = False

    def __init__(
        self,
        logger: ILogger,
        node_definition_repo: INodeDefinitionRepository,
        worker_registry_client: Optional[Any] = None,
        variable_resolver: Optional[Any] = None,
        handler_registry: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.node_definition_repo = node_definition_repo
        self._worker_client = worker_registry_client
        self._resolver = variable_resolver
        self._handler_registry = handler_registry
        self._runner = (
            StandaloneNodeRunner(handler_registry, variable_resolver)
            if handler_registry is not None
            else None
        )

    async def execute(self, command: TestNodeDefinitionCommand) -> TestNodeDefinitionResult:
        self.logger.info(
            "Testing node definition",
            node_definition_id=command.node_definition_id,
        )

        # 1. Load node definition (base_worker_id is already normalized to the
        #    bare worker_type by NodeDefinition.__post_init__).
        node_def = self.node_definition_repo.get_by_id(command.node_definition_id)
        if not node_def:
            return TestNodeDefinitionResult(
                node_definition_id=command.node_definition_id,
                error=ErrorCode.NODE_DEFINITION_NOT_FOUND.default_message,
                error_code=ErrorCode.NODE_DEFINITION_NOT_FOUND.code,
                error_details={"node_definition_id": command.node_definition_id},
            )

        # 2. Route by base_type.
        base_type = node_def.base_type
        if base_type == NodeType.TASK:
            return await self._test_worker(command, node_def)

        if self._runner is not None and self._runner.is_inline_testable(base_type):
            return await self._test_engine_builtin(command, node_def)

        # PENDING or deferring ENGINE (LOOP/WORKFLOW/PARALLEL/…): not testable here.
        reason = (
            "pending"
            if base_type in node_types_for_runtime(NodeRuntime.PENDING)
            else "deferring_engine"
        )
        return TestNodeDefinitionResult(
            node_definition_id=command.node_definition_id,
            node_type=base_type.value,
            error=ErrorCode.NODE_NOT_TESTABLE_STANDALONE.default_message,
            error_code=ErrorCode.NODE_NOT_TESTABLE_STANDALONE.code,
            error_details={"base_type": base_type.value, "reason": reason},
        )

    # ── TASK / worker path ───────────────────────────────────────────
    async def _test_worker(
        self,
        command: TestNodeDefinitionCommand,
        node_def: Any,
    ) -> TestNodeDefinitionResult:
        if self._worker_client is None or not hasattr(self._worker_client, "execute_task"):
            return TestNodeDefinitionResult(
                node_definition_id=command.node_definition_id,
                node_type=NodeType.TASK.value,
                error=ErrorCode.SERVICE_NOT_CONFIGURED.default_message,
                error_code=ErrorCode.SERVICE_NOT_CONFIGURED.code,
                error_details={"service": "worker_execution"},
            )

        if not node_def.base_worker_id:
            return TestNodeDefinitionResult(
                node_definition_id=command.node_definition_id,
                node_type=NodeType.TASK.value,
                error=ErrorCode.VALIDATION_ERROR.default_message,
                error_code=ErrorCode.VALIDATION_ERROR.code,
                error_details={"node_definition_id": command.node_definition_id},
            )

        # Apply input_mapping then merge locked_defaults
        # (mirrors task_handler._apply_node_definition:
        #  result = {**user_input, **mapped, **locked_defaults}).
        input_mapping = node_def.input_mapping or {}
        if input_mapping and self._resolver:
            mapping_context = {"custom": command.input_data}
            try:
                mapped = await self._resolver.resolve_mapping(input_mapping, mapping_context)
            except Exception:
                mapped = {}
            resolved_input = {**command.input_data, **mapped, **node_def.locked_defaults}
        else:
            resolved_input = {**command.input_data, **node_def.locked_defaults}

        try:
            worker_result = self._worker_client.execute_task(
                worker_type=node_def.base_worker_id,
                input_data=resolved_input,
            )
        except Exception as exc:
            self.logger.error(
                "Worker execution failed",
                node_definition_id=command.node_definition_id,
                error=str(exc),
            )
            return TestNodeDefinitionResult(
                node_definition_id=command.node_definition_id,
                node_type=NodeType.TASK.value,
                worker_type=node_def.base_worker_id,
                resolved_input=resolved_input,
                status="FAILED",
                error=str(exc),
            )

        status = worker_result.get("status", "FAILED")
        # A worker reports terminal status as "success"/"error" (worker_sdk
        # TaskStatus); "COMPLETED" is the engine-inline marker. The old
        # `== "FAILED"` gate only caught client-side failures, so a worker that
        # ran but failed the task (status "error", e.g. "'code' is required.")
        # slipped through with its message dropped. Treat anything that isn't a
        # success marker as a failure and surface its error.
        is_success = status in ("success", "COMPLETED")
        error = ""
        if not is_success:
            err_data = worker_result.get("error", "")
            error = err_data if isinstance(err_data, str) else str(err_data)

        raw_outputs = worker_result.get("outputs", worker_result.get("output_metadata", {}))

        # Apply output_mapping so the test surfaces only the user-defined output
        # keys — not the raw worker response. Mirrors complete_task_use_case:
        # resolve_mapping(output_mapping, {"_output": <worker output>}). When no
        # mapping is defined the raw output passes through (same as production).
        outputs = raw_outputs
        output_mapping = node_def.output_mapping or {}
        if is_success and output_mapping and self._resolver and isinstance(raw_outputs, dict):
            try:
                outputs = await self._resolver.resolve_mapping(output_mapping, {"_output": raw_outputs})
            except Exception as exc:
                self.logger.warning(
                    "output_mapping failed during node definition test",
                    node_definition_id=command.node_definition_id,
                    error=str(exc),
                )

        return TestNodeDefinitionResult(
            node_definition_id=command.node_definition_id,
            node_type=NodeType.TASK.value,
            worker_type=node_def.base_worker_id,
            resolved_input=resolved_input,
            status=status,
            outputs=outputs,
            output_reference=worker_result.get("output_reference", ""),
            execution_time_ms=int(worker_result.get("execution_time_ms", 0)),
            error=error,
        )

    # ── ENGINE builtin path ──────────────────────────────────────────
    async def _test_engine_builtin(
        self,
        command: TestNodeDefinitionCommand,
        node_def: Any,
    ) -> TestNodeDefinitionResult:
        try:
            handler_result = await self._runner.run(node_def, command.input_data)
        except NotTestableStandalone:
            return TestNodeDefinitionResult(
                node_definition_id=command.node_definition_id,
                node_type=node_def.base_type.value,
                error=ErrorCode.NODE_NOT_TESTABLE_STANDALONE.default_message,
                error_code=ErrorCode.NODE_NOT_TESTABLE_STANDALONE.code,
                error_details={"base_type": node_def.base_type.value, "reason": "deferring_engine"},
            )
        except Exception as exc:
            self.logger.error(
                "Builtin node test failed",
                node_definition_id=command.node_definition_id,
                error=str(exc),
            )
            return TestNodeDefinitionResult(
                node_definition_id=command.node_definition_id,
                node_type=node_def.base_type.value,
                status="FAILED",
                error=str(exc),
            )

        node_output = handler_result.state_updates.get(node_def.id, {}) or {}
        outputs = dict(node_output.get("output", {}) or {})
        # SET_VARIABLE writes the merged variables into the __vars slot rather
        # than node output — surface them so the tester can see what was set.
        if "__vars" in handler_result.state_updates:
            outputs["__vars"] = handler_result.state_updates["__vars"]

        return TestNodeDefinitionResult(
            node_definition_id=command.node_definition_id,
            node_type=node_def.base_type.value,
            status="COMPLETED",
            outputs=outputs,
            handler_output=node_output,
            branch=handler_result.completion_payload.get("branch", ""),
        )
