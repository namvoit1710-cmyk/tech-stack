"""Dedicated use-case: validate / execute a rule plan against a file (default flow).

Framework-independent: composes Layer-1 entities (ExecutionPlan, ExecutionResult,
AgentResponse) over Layer-2 ports. Any failure returns a 200 AgentResponse error
envelope. No `Any`; dynamic external JSON is `dict[str, object]`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import cast

from app.layer1_domain.entities.agent_response import AgentResponse
from app.layer1_domain.entities.execution_plan import ExecutionPlan
from app.layer1_domain.entities.execution_result import ExecutionResult
from app.layer1_domain.entities.template_field import TemplateField
from app.layer2_application.interfaces.ports import (
    ColumnExecutorPort,
    FileServicePort,
    GovernancePort,
    HeaderInferencePort,
    PlannerPort,
    SummaryPort,
)


@dataclass
class ExecuteValidationInput:
    message: str = ""
    file_id: str | None = None
    rules: list[dict[str, object]] | dict[str, object] | None = None
    header: dict[str, object] | None = None
    template_id: str = ""

    @classmethod
    def from_params(cls, message: str, params: dict[str, object]) -> "ExecuteValidationInput":
        rules = params.get("rules")
        file_id = params.get("file_id")
        header = params.get("header")
        template_id = params.get("template_id")
        return cls(
            message=message,
            file_id=file_id if isinstance(file_id, str) else None,
            rules=cast("list[dict[str, object]] | dict[str, object]", rules) if isinstance(rules, (list, dict)) else None,
            header=cast("dict[str, object]", header) if isinstance(header, dict) else None,
            template_id=template_id if isinstance(template_id, str) else "",
        )


class ExecuteValidationUseCase:
    def __init__(
        self,
        file_service: FileServicePort,
        header_inference: HeaderInferencePort,
        planner: PlannerPort,
        column_executor: ColumnExecutorPort,
        summary_service: SummaryPort | None,
        governance: GovernancePort,
        agent_type: str,
    ) -> None:
        self._file_service = file_service
        self._header_inference = header_inference
        self._planner = planner
        self._column_executor = column_executor
        self._summary_service = summary_service
        self._governance = governance
        self._agent_type = agent_type

    async def execute(self, request: ExecuteValidationInput) -> AgentResponse:
        try:
            aliases: list[str] = []
            header_from_template: dict[str, object] | None = None
            if request.template_id:
                fields = self._governance.get_template_fields(request.template_id)
                aliases, header_from_template = TemplateField.build_header(fields)

            file_id, extension, header_info = await self._prepare_execution_context(request)
            if aliases:
                header_info = header_from_template
            plan = await self._resolve_execution_plan(request.message, header_info, request.rules)
            logging.info("step=generate_execution_plan steps=%d", len(plan.steps))

            if plan.is_not_supported():
                return AgentResponse.not_supported(self._agent_type, plan.message)

            if not file_id:
                raw: dict[str, object] = {}
                result = ExecutionResult.rule_only(plan.steps)
            else:
                raw = self._execute_plan(file_id, extension, plan)
                result = ExecutionResult.from_executor(raw)

            message_text = await self._resolve_message_text(raw, result)
            return AgentResponse.success(message_text, result, self._agent_type)
        except Exception as e:
            logging.exception("execute_validation_failed")
            return AgentResponse.error(e, self._agent_type)

    # ---------------- helpers (orchestration only) ---------------- #

    async def _prepare_execution_context(
        self, request: ExecuteValidationInput
    ) -> tuple[str | None, str, dict[str, object] | None]:
        file_id = request.file_id
        header_info = request.header
        rules = request.rules
        extension = "csv"

        if not file_id:
            logging.info("step=skip_validation_execution reason=no_file_id")
            default_header: dict[str, object] = {"header": {"header_columns": []}, "num_rows": 0, "num_columns": 0}
            return None, extension, header_info or default_header

        is_csv = file_id.lower().endswith(".csv")
        if not is_csv:
            file_bytes, extension = self._file_service.download(file_id)
            logging.info("step=download_file file_id=%s extension=%s size=%d", file_id, extension, len(file_bytes))
            converted = self._file_service.convert_to_csv_and_upload(file_bytes, extension)
            if converted is not None:
                file_id, file_bytes, extension = converted
            logging.info("step=convert_file_to_csv file_id=%s extension=%s", file_id, extension)

        header_info = None
        if rules is None:
            preview = self._file_service.fetch_preview_file_data(file_id)
            header_info = await self._resolve_header_info(None, json.dumps(preview))
            logging.info("step=load_header_config header=%s", header_info)

        return file_id, extension, header_info

    async def _resolve_header_info(
        self, header_info: dict[str, object] | None, preview_text: str
    ) -> dict[str, object]:
        if header_info:
            return header_info
        return await self._header_inference.get_header_by_llm(preview_text)

    async def _resolve_execution_plan(
        self, message: str, header_info: dict[str, object] | None,
        rules: list[dict[str, object]] | dict[str, object] | None,
    ) -> ExecutionPlan:
        if rules is not None:
            logging.info("step=use_rules_from_parameters")
            return ExecutionPlan.from_rules(rules)
        return ExecutionPlan.from_planner(await self._planner.plan(user_prompt=message, header=header_info))

    def _execute_plan(self, file_id: str, extension: str, plan: ExecutionPlan) -> dict[str, object]:
        same_scope_result = self._execute_same_scope_steps(file_id, extension, plan)
        if same_scope_result is not None:
            return same_scope_result

        result: dict[str, object] = {}
        executed_rules: list[dict[str, object]] = []
        for scope, batch_steps in plan.grouped_by_scope():
            logging.info("step=execute_scope scope=%s", scope)
            executor = {"column": self._column_executor.execute_batch}.get(scope)
            if not executor:
                raise ValueError(f"Unsupported scope: {scope}")

            result = executor(file_id, extension, batch_steps)
            if not isinstance(result, dict):
                raise ValueError("Validation executor must return a dictionary result")
            executed_rules.extend(batch_steps)

        result["rules"] = executed_rules
        return result

    def _execute_same_scope_steps(
        self, current_file: str, file_format: str, plan: ExecutionPlan
    ) -> dict[str, object] | None:
        combined_scope = plan.single_scope()
        if combined_scope is None:
            return None

        executor = {"column": self._column_executor.execute_column_operations}.get(combined_scope)
        if not executor:
            raise ValueError(f"Unsupported scope: {combined_scope}")

        logging.info("step=execute_combined_scope scope=%s steps=%d", combined_scope, len(plan.steps))
        result = executor(current_file, file_format, plan.steps)
        if not isinstance(result, dict):
            raise ValueError("Validation executor must return a dictionary result")

        result["rules"] = list(plan.steps)
        return result

    async def _resolve_message_text(self, raw: dict[str, object], result: ExecutionResult) -> str:
        # The layer-4 summary reads the fuller data-factory payload, so it takes the raw
        # dict at its own boundary; ExecutionResult stays a clean typed entity.
        if self._summary_service and result.has_result:
            try:
                return await self._summary_service.generate_summary(raw)
            except Exception:
                logging.exception("step=generate_summary_failed fallback_to_original")
                return result.message or "Validation completed."
        return result.message or "Validation completed."
