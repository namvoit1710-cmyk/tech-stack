import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.layer1_domain.entities.execution_plan import ExecutionPlan
from app.layer1_domain.entities.file_state import ConversationTurn
from app.layer2_application.interfaces.structured_llm_gateway import (
    IStructuredLLMGateway,
)

logger = logging.getLogger(__name__)


@dataclass
class PlanInputDto:
    """Input DTO for Plan Use Case."""

    message: str
    file_id: Optional[str] = None
    conversation_history: List[ConversationTurn] = field(default_factory=list)
    tool_catalog: List[Dict[str, Any]] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanOutputDto:
    """Output DTO for Plan Use Case."""

    execution_plan: ExecutionPlan
    is_reviewed: bool = False


class PlanUseCase:
    """
    Use case for generating execution plans from user requests.

    Uses a structured LLM gateway when configured, with a deterministic
    rule-based fallback for local/dev/test runs.
    """

    _READ_ONLY_QUERY_KEYWORDS = (
        "show",
        "get rows",
        "query",
        "read",
        "preview",
        "select",
        "where",
        "filter",
        "order by",
        "sort",
        "$filter",
        "$select",
        "$orderby",
        "odata",
    )

    _CREATE_NEW_FILE_KEYWORDS = (
        "create new file",
        "save",
        "export",
        "download",
        "produce",
        "generate file",
        "new filtered file",
        "transform",
        "apply transformation",
    )

    def __init__(
        self,
        structured_llm_gateway: IStructuredLLMGateway | None = None,
    ):
        self.structured_llm_gateway = structured_llm_gateway

    async def execute(self, input_dto: PlanInputDto) -> PlanOutputDto:
        logger.info("Creating execution plan for message: %s", input_dto.message[:100])

        system_prompt = self._build_system_prompt(input_dto.tool_catalog or [])
        user_prompt = self._build_user_prompt(input_dto)

        plan_dict: Dict[str, Any]
        if self.structured_llm_gateway is not None:
            logger.info(
                "PlanUseCase using structured LLM gateway: gateway=%s message=%r "
                "file_id=%s parameter_keys=%s tool_names=%s system_prompt_chars=%s "
                "user_prompt_chars=%s",
                f"{type(self.structured_llm_gateway).__module__}."
                f"{type(self.structured_llm_gateway).__name__}",
                input_dto.message,
                input_dto.file_id,
                sorted((input_dto.parameters or {}).keys()),
                [
                    str(tool.get("name", "unknown"))
                    for tool in (input_dto.tool_catalog or [])
                    if isinstance(tool, dict)
                ],
                len(system_prompt),
                len(user_prompt),
            )
            try:
                plan_dict = await self._generate_plan_with_llm(
                    system_prompt, user_prompt
                )
            except (KeyError, TypeError, ValueError) as error:
                logger.warning(
                    "LLM plan generation failed, falling back to rule-based plan: %s",
                    error,
                )
                plan_dict = self._create_fallback_plan(input_dto)
        else:
            plan_dict = self._create_fallback_plan(input_dto)

        plan_dict = self._add_extract_fields_hint(plan_dict, input_dto)

        try:
            execution_plan = ExecutionPlan.from_dict(plan_dict)
            execution_plan.validate(
                allowed_tools=self._available_tool_names(input_dto.tool_catalog)
            )
        except (KeyError, TypeError, ValueError) as error:
            logger.warning(
                "ExecutionPlan construction failed, falling back to rule-based plan: %s",
                error,
            )
            execution_plan = ExecutionPlan.from_dict(
                self._create_fallback_plan(input_dto)
            )
            execution_plan.validate(
                allowed_tools=self._available_tool_names(input_dto.tool_catalog)
            )

        logger.info("Generated plan with %d steps", len(execution_plan.steps))

        return PlanOutputDto(
            execution_plan=execution_plan,
            is_reviewed=False,
        )

    async def _generate_plan_with_llm(
        self, system_prompt: str, user_prompt: str
    ) -> Dict[str, Any]:
        """Use the injected structured LLM gateway and return a plan dict."""

        if self.structured_llm_gateway is None:
            raise TypeError("Structured LLM gateway is not configured")

        return await self.structured_llm_gateway.generate_execution_plan(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    def _build_system_prompt(self, tool_catalog: List[Dict]) -> str:
        tools_description = "\n".join(
            [
                f"- {tool.get('name', 'unknown')}: {tool.get('description', '')}"
                for tool in tool_catalog
            ]
        )

        return f"""You are a CSV processing assistant that creates execution plans.

Available tools:
{tools_description}

Your task is to analyze the user's request and create a step-by-step execution plan.

IMPORTANT TOOL SELECTION RULES:
1. If user provides 'json_data' parameter, use 'convert_json_to_csv'.
2. If user mentions "convert JSON" or "JSON to CSV", use 'convert_json_to_csv'.
3. If user has file_id and mentions column operations that CREATE a new file (rename, add, drop, mapping, save/export filtered file), use 'transform_column'.
4. If user has file_id and mentions row mutations (insert rows, delete rows), use 'transform_row'.
5. If user has file_id and asks to show/query/read/filter/select/order rows WITHOUT creating a new file, use 'query_file_data'.
6. Use 'query_file_data' for read-only OData queries such as $filter, $select, $orderby, $top, $skip, $count.
7. Use 'transform_column' filter ONLY when the user wants to create/save/export a NEW filtered file.
8. If values are needed later to fill workflow/input fields, use 'query_file_data' with input.extract_fields="*" so it returns output_contract="extracted_fields.v1".
9. Parameters are more important than message text.

Output JSON format:
{{
    "goal": "short goal",
    "steps": [
        {{
            "order": 1,
            "tool": "tool_name",
            "input": {{}},
            "reason": "Why this step is needed"
        }}
    ]
}}

For most tools, leave "input" empty {{}} because request parameters are auto-merged during execution.
Exception: for query_file_data, include "input": {{"odata_query": "<OData query>"}} when the user asks a natural-language read-only query and no explicit odata_query parameter was provided. Include "extract_fields": "*" when values should be taken from the file for workflow/input fields.
Choose the most appropriate tool based on parameters AND user's intent."""

    def _build_user_prompt(self, input_dto: PlanInputDto) -> str:
        prompt_parts = [f"User request: {input_dto.message}"]

        if input_dto.file_id:
            prompt_parts.append(f"File ID: {input_dto.file_id}")

        if input_dto.parameters:
            params_info = []
            if "json_data" in input_dto.parameters:
                params_info.append("CONTAINS json_data -> Use convert_json_to_csv tool")
            if "odata_query" in input_dto.parameters:
                params_info.append("CONTAINS odata_query -> Use query_file_data tool")
            if "file_id" in input_dto.parameters:
                params_info.append(
                    "File ID provided -> Can use file query/transformation tools"
                )

            params_summary = "; ".join(params_info) if params_info else ""
            prompt_parts.append(
                f"Parameters: {json.dumps(input_dto.parameters, default=str)}"
            )
            if params_summary:
                prompt_parts.append(f"Parameter hints: {params_summary}")

        if input_dto.conversation_history:
            history = "\n".join(
                [
                    f"{turn.role}: {turn.message}"
                    for turn in input_dto.conversation_history[-3:]
                ]
            )
            prompt_parts.append(f"Recent conversation:\n{history}")

        return "\n\n".join(prompt_parts)

    def _create_fallback_plan(self, input_dto: PlanInputDto) -> Dict[str, Any]:
        params = input_dto.parameters or {}
        message_lower = (input_dto.message or "").lower()

        step_input: Dict[str, Any] = {}

        if "json_data" in params:
            tool_name = "convert_json_to_csv"
        elif "odata_query" in params:
            tool_name = "query_file_data"
        elif self._is_csv_to_json_request(message_lower):
            tool_name = "convert_csv_to_json"
        elif self._is_json_to_csv_request(message_lower):
            tool_name = "convert_json_to_csv"
        elif self._is_read_only_query_request(input_dto):
            tool_name = "query_file_data"
            derived_query = self._derive_odata_query_from_message(input_dto.message)
            if derived_query:
                step_input["odata_query"] = derived_query
        elif self._is_create_new_file_request(input_dto):
            tool_name = "transform_column"
        elif any(
            word in message_lower for word in ("insert row", "delete row", "remove row")
        ):
            tool_name = "transform_row"
        elif any(
            word in message_lower
            for word in ("transform", "rename", "add column", "drop column", "mapping")
        ):
            tool_name = "transform_column"
        else:
            tool_name = self._first_available_tool(input_dto.tool_catalog)

        return {
            "goal": input_dto.message or "",
            "steps": [
                {
                    "order": 1,
                    "tool": tool_name,
                    "input": step_input,
                    "reason": f"Execute {tool_name} based on user request",
                }
            ],
        }

    @staticmethod
    def _is_csv_to_json_request(message_lower: str) -> bool:
        if "convert" not in message_lower:
            return False
        return any(
            phrase in message_lower
            for phrase in (
                "csv to json",
                "csv into json",
                "to json",
                "as json",
                "json file from csv",
            )
        )

    @staticmethod
    def _is_json_to_csv_request(message_lower: str) -> bool:
        if "convert" not in message_lower and "json to csv" not in message_lower:
            return False
        return any(
            phrase in message_lower
            for phrase in (
                "json to csv",
                "json into csv",
                "convert json",
                "to csv",
                "as csv",
                "csv file from json",
            )
        )

    def _is_read_only_query_request(self, input_dto: PlanInputDto) -> bool:
        if not input_dto.file_id:
            return False

        message_lower = (input_dto.message or "").lower()
        if not any(
            keyword in message_lower for keyword in self._READ_ONLY_QUERY_KEYWORDS
        ):
            return False

        return not any(
            keyword in message_lower for keyword in self._CREATE_NEW_FILE_KEYWORDS
        )

    def _is_create_new_file_request(self, input_dto: PlanInputDto) -> bool:
        """Detect read/filter requests that explicitly ask for a new output file."""
        if not input_dto.file_id:
            return False

        if not self._tool_available(input_dto.tool_catalog, "transform_column"):
            return False

        message_lower = (input_dto.message or "").lower()
        has_file_output_intent = any(
            keyword in message_lower for keyword in self._CREATE_NEW_FILE_KEYWORDS
        )
        has_filter_or_projection_intent = any(
            keyword in message_lower for keyword in self._READ_ONLY_QUERY_KEYWORDS
        )
        return has_file_output_intent and has_filter_or_projection_intent

    def _add_extract_fields_hint(
        self, plan_dict: Dict[str, Any], input_dto: PlanInputDto
    ) -> Dict[str, Any]:
        if not input_dto.file_id:
            return plan_dict

        lower = (input_dto.message or "").lower()

        if "file" in lower and ("workflow" in lower or "wf" in lower):
            field = "*"
        else:
            field = self._derive_extract_field_from_message(input_dto.message)

        if not field:
            return plan_dict

        for step in plan_dict.get("steps", []) or []:
            if isinstance(step, dict) and step.get("tool") == "query_file_data":
                step_input = step.setdefault("input", {})
                if isinstance(step_input, dict):
                    step_input.setdefault("extract_fields", field)

        return plan_dict

    @staticmethod
    def _derive_extract_field_from_message(message: str) -> str:
        match = re.search(
            r"\bvalue\s+of\s+([A-Za-z_][A-Za-z0-9_ -]*)",
            message or "",
            re.IGNORECASE,
        )
        return match.group(1).strip() if match else ""

    @staticmethod
    def _derive_odata_query_from_message(message: str) -> str:
        """Best-effort local fallback only. The LLM should produce richer OData."""

        text = (message or "").strip()
        lower = text.lower()
        parts: list[str] = []

        if (
            "$filter=" in lower
            or "$select=" in lower
            or "$orderby=" in lower
            or "$top=" in lower
        ):
            idxs = [
                i
                for i in (
                    lower.find("$filter="),
                    lower.find("$select="),
                    lower.find("$orderby="),
                    lower.find("$top="),
                )
                if i >= 0
            ]
            return text[min(idxs) :].lstrip("?")

        top_match = re.search(
            r"\btop\s+(\d+)\b|\bfirst\s+(\d+)\b|\blimit\s+(\d+)\b", lower
        )
        if top_match:
            top = next(group for group in top_match.groups() if group)
            parts.append(f"$top={top}")

        select_match = re.search(
            r"\bselect\s+([A-Za-z0-9_.,\s-]+?)(?:\s+where\b|\s+order\s+by\b|$)",
            text,
            re.IGNORECASE,
        )
        if select_match:
            columns = ",".join(
                col.strip()
                for col in re.split(r"[,\s]+", select_match.group(1))
                if col.strip()
                and col.strip().lower() not in {"and", "columns", "column"}
            )
            if columns:
                parts.append(f"$select={columns}")

        order_match = re.search(
            r"order\s+by\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+(asc|desc))?",
            text,
            re.IGNORECASE,
        )
        if order_match:
            direction = order_match.group(2) or "asc"
            parts.append(f"$orderby={order_match.group(1)} {direction.lower()}")

        where_match = re.search(
            r"(?:where|filter)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|eq|is)\s*['\"]?([^'\"\s,]+)['\"]?",
            text,
            re.IGNORECASE,
        )
        if where_match:
            column, value = where_match.group(1), where_match.group(2)
            if re.fullmatch(r"-?\d+(?:\.\d+)?", value) or value.lower() in {
                "true",
                "false",
                "null",
            }:
                rendered_value = value.lower()
            else:
                rendered_value = f"'{value}'"
            parts.append(f"$filter={column} eq {rendered_value}")

        return "&".join(parts)

    @staticmethod
    def _tool_available(
        tool_catalog: List[Dict[str, Any]] | None, tool_name: str
    ) -> bool:
        if not tool_catalog:
            return True
        return any(str(tool.get("name") or "") == tool_name for tool in tool_catalog)

    @staticmethod
    def _first_available_tool(tool_catalog: List[Dict[str, Any]] | None) -> str:
        if not tool_catalog:
            return "unknown"
        first = tool_catalog[0]
        return str(first.get("name") or "unknown")

    @staticmethod
    def _available_tool_names(
        tool_catalog: List[Dict[str, Any]] | None,
    ) -> set[str] | None:
        if not tool_catalog:
            return None
        return {
            str(tool.get("name") or "")
            for tool in tool_catalog
            if isinstance(tool, dict) and str(tool.get("name") or "")
        }
