from __future__ import annotations

import inspect
import json
import logging
from typing import Any

from app.layer1_domain.entities.csv_execution_result import (
    CSVExecutionResult,
    StepResult,
)
from app.layer1_domain.entities.execution_plan import ExecutionPlan
from app.layer1_domain.entities.file_state import FileState

logger = logging.getLogger(__name__)


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", getattr(tool, "__name__", "unknown")))


def _schema_dict(args_schema: Any) -> dict[str, Any]:
    if args_schema is None:
        return {}

    model_json_schema = getattr(args_schema, "model_json_schema", None)
    if callable(model_json_schema):
        try:
            schema = model_json_schema()
            return schema if isinstance(schema, dict) else {}
        except Exception:
            pass

    schema_func = getattr(args_schema, "schema", None)
    if callable(schema_func):
        try:
            schema = schema_func()
            return schema if isinstance(schema, dict) else {}
        except Exception:
            pass

    return {}


def _tool_properties(tool: Any) -> dict[str, Any]:
    return _schema_dict(getattr(tool, "args_schema", None)).get("properties", {}) or {}


def _filter_input_for_tool(tool: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Use the SDK/LangChain tool schema to avoid passing unrelated state fields."""

    properties = _tool_properties(tool)
    if not properties:
        return dict(payload)

    allowed = set(properties.keys())
    return {key: value for key, value in payload.items() if key in allowed}


def _serialize_values_for_schema(tool: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Auto-serialize dict/list values for string-typed tool parameters."""

    properties = _tool_properties(tool)
    if not properties:
        return payload

    normalized = dict(payload)
    for param_name, param_value in list(normalized.items()):
        param_schema = properties.get(param_name) or {}
        expected_type = param_schema.get("type")

        if expected_type == "string" and isinstance(param_value, (dict, list)):
            normalized[param_name] = json.dumps(param_value, ensure_ascii=False)

    return normalized


async def _invoke_tool(tool: Any, payload: dict[str, Any]) -> Any:
    ainvoke = getattr(tool, "ainvoke", None)
    if callable(ainvoke):
        return await ainvoke(payload)

    invoke = getattr(tool, "invoke", None)
    if callable(invoke):
        return invoke(payload)

    if inspect.iscoroutinefunction(tool):
        return await tool(**payload)

    if callable(tool):
        return tool(**payload)

    raise TypeError(f"Tool {_tool_name(tool)!r} is not invokable")


def _coerce_tool_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result

    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"value": result}

    return {"value": result}


async def execute_node(state: FileState, deps: dict) -> dict:
    """
    Execute node: execute the plan using SDK-registered worker tools.

    Important runtime safeguards:
    - validates execution-plan and execution-result domain invariants
    - filters merged state/input to the selected tool's SDK schema
    - supports SDK LangChain tools via ainvoke/invoke
    - updates file_id between chained steps when a transform creates a new file
    """
    execution_plan_dict = state.get("execution_plan", {})
    worker_tools = deps.get("worker_tools", [])
    request_parameters = dict(state.get("parameters", {}) or {})

    execution_plan = ExecutionPlan.from_dict(execution_plan_dict)
    execution_plan.validate()
    tool_lookup = {_tool_name(tool): tool for tool in worker_tools}

    step_results: list[StepResult] = []
    final_file_id = str(request_parameters.get("file_id", "") or "")
    ordered_steps = execution_plan.ordered_steps()

    for step in ordered_steps:
        merged_input = {**request_parameters, **(step.input or {})}

        tool = tool_lookup.get(step.tool)
        if not tool:
            logger.error("Tool %r not found in registry", step.tool)
            step_results.append(
                StepResult(
                    order=step.order,
                    tool=step.tool,
                    success=False,
                    result={},
                    error=f"Tool {step.tool!r} not found",
                )
            )
            continue

        filtered_input = _filter_input_for_tool(tool, merged_input)
        tool_input = _serialize_values_for_schema(tool, filtered_input)

        logger.info(
            "TOOL SELECTED: name=%s params=%s",
            step.tool,
            sorted(tool_input.keys()),
        )

        try:
            raw_result = await _invoke_tool(tool, tool_input)
            result = _coerce_tool_result(raw_result)

            returned_file_id = result.get("file_id")
            if isinstance(returned_file_id, str) and returned_file_id:
                final_file_id = returned_file_id
                request_parameters["file_id"] = returned_file_id

            step_results.append(
                StepResult(
                    order=step.order,
                    tool=step.tool,
                    success=True,
                    result=result,
                )
            )
        except Exception as exc:
            logger.exception("Tool %s failed", step.tool)
            step_results.append(
                StepResult(
                    order=step.order,
                    tool=step.tool,
                    success=False,
                    result={},
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    success = bool(step_results) and all(step.success for step in step_results)
    failed_count = sum(1 for step in step_results if not step.success)
    summary = (
        f"Executed {len(step_results)} step(s) successfully."
        if success
        else f"Executed {len(step_results)} step(s); {failed_count} failed."
    )

    result_entity = CSVExecutionResult(
        success=success,
        step_results=step_results,
        final_file_id=final_file_id or None,
        error=None if success else summary,
        summary=summary,
    )
    result_entity.validate()
    execution_result = result_entity.to_dict()

    return {
        "execution_result": execution_result,
        "agent_result": execution_result,
        "final_file_id": final_file_id or None,
        "parameters": request_parameters,
    }
