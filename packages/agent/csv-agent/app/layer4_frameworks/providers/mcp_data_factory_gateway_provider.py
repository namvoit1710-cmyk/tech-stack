from __future__ import annotations

import json
import logging
from typing import Any

from agent_sdk import IMCPClientService


class MCPDataFactoryGatewayProvider:
    """Data Factory gateway backed by SDK MCPClientService tool wrappers."""

    def __init__(
        self,
        *,
        mcp_client: IMCPClientService,
        settings_provider: Any,
        logger: Any | None = None,
    ) -> None:
        self._mcp_client = mcp_client
        self._settings_provider = settings_provider
        self._logger = logger or logging.getLogger(__name__)
        self._tool_cache: dict[str, Any] = {}
        self._file_arg_cache: dict[str, str] = {}

    async def validate_file(
        self,
        *,
        file_path: str,
        file_format: str = "csv",
        rules: list[dict[str, Any]] | None = None,
        rule_set_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._call_mcp_tool(
            "validate_file",
            {
                "__file_ref__": file_path,
                "file_format": file_format,
                "rules": rules,
                "rule_set_id": rule_set_id,
            },
        )

    async def transform_file(
        self,
        *,
        file_path: str,
        rules: list[dict[str, Any]],
        file_format: str = "csv",
        output_format: str = "csv",
    ) -> dict[str, Any]:
        return await self._call_mcp_tool(
            "transform_file",
            {
                "__file_ref__": file_path,
                "rules": rules,
                "file_format": file_format,
                "output_format": output_format,
            },
        )

    async def inspect_file(
        self,
        *,
        file_path: str,
        file_format: str = "csv",
        sample_size: int = 5,
        sheet_names: list[str] | None = None,
        merge_sheets: bool = False,
    ) -> dict[str, Any]:
        return await self._call_mcp_tool(
            "inspect_file",
            {
                "__file_ref__": file_path,
                "file_format": file_format,
                "sample_size": sample_size,
                "sheet_names": sheet_names,
                "merge_sheets": merge_sheets,
            },
        )

    async def list_rule_sets(self) -> dict[str, Any]:
        return await self._call_mcp_tool("list_rule_sets", {})

    async def transform_rows(
        self,
        *,
        file_path: str,
        operations: list[dict[str, Any]],
        file_format: str = "csv",
        use_column_indices: bool = False,
    ) -> dict[str, Any]:
        return await self._call_mcp_tool(
            "transform_rows",
            {
                "__file_ref__": file_path,
                "operations": operations,
                "file_format": file_format,
                "use_column_indices": use_column_indices,
            },
        )

    async def _get_tool(self, tool_name: str) -> Any:
        cached = self._tool_cache.get(tool_name)
        if cached is not None:
            return cached
        tools = await self._mcp_client.get_filtered_tools([tool_name])
        for tool in tools:
            metadata = getattr(tool, "metadata", None) or {}
            if (
                metadata.get("mcp_raw_name") == tool_name
                or getattr(tool, "name", "") == tool_name
            ):
                self._tool_cache[tool_name] = tool
                return tool
        raise RuntimeError(f"Data Factory MCP tool {tool_name!r} is not available")

    def _schema_dict(self, schema_model: Any) -> dict[str, Any]:
        if schema_model is None:
            return {}
        if isinstance(schema_model, dict):
            return schema_model
        model_json_schema = getattr(schema_model, "model_json_schema", None)
        if callable(model_json_schema):
            schema = model_json_schema()
            return schema if isinstance(schema, dict) else {}
        legacy_schema = getattr(schema_model, "schema", None)
        if callable(legacy_schema):
            schema = legacy_schema()
            return schema if isinstance(schema, dict) else {}
        return {}

    def _resolve_file_arg(self, tool: Any, tool_name: str, file_ref: str) -> str:
        cached = self._file_arg_cache.get(tool_name)
        if cached:
            return cached
        schema = self._schema_dict(getattr(tool, "args_schema", None))
        properties = schema.get("properties", {}) or {}
        required = set(schema.get("required", []) or [])
        if "file_id" in required:
            resolved = "file_id"
        elif "file_path" in required:
            resolved = "file_path"
        elif "file_id" in properties and "file_path" not in properties:
            resolved = "file_id"
        elif "file_path" in properties and "file_id" not in properties:
            resolved = "file_path"
        elif "file_id" in properties and str(file_ref).startswith("file_"):
            resolved = "file_id"
        else:
            resolved = "file_path"
        self._file_arg_cache[tool_name] = resolved
        return resolved

    def _build_tool_arguments(
        self, tool: Any, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if "__file_ref__" not in arguments:
            return dict(arguments)
        file_ref = arguments.get("__file_ref__")
        final_args = dict(arguments)
        final_args.pop("__file_ref__", None)
        final_args[self._resolve_file_arg(tool, tool_name, str(file_ref))] = file_ref
        return final_args

    def _extract_text(self, content: Any) -> str | None:
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            text = content.get("text")
            return text if isinstance(text, str) else None
        text = getattr(content, "text", None)
        return text if isinstance(text, str) else None

    def _coerce_tool_result(self, result: Any) -> dict[str, Any]:
        if isinstance(result, tuple) and result:
            result = result[0]
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            for item in result:
                text = self._extract_text(item)
                if text:
                    try:
                        parsed = json.loads(text)
                    except json.JSONDecodeError:
                        return {"result": text}
                    return parsed if isinstance(parsed, dict) else {"result": parsed}
            return {"result": result}
        text = self._extract_text(result)
        if text is not None:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return {"result": text}
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        return {"result": result}

    def _collect_sub_exceptions(self, error: BaseException) -> list[str]:
        nested = getattr(error, "exceptions", None)
        if isinstance(nested, (list, tuple)) and nested:
            details: list[str] = []
            for sub_error in nested:
                details.extend(self._collect_sub_exceptions(sub_error))
            return details
        return [f"{type(error).__name__}: {error}"]

    async def _call_mcp_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            tool = await self._get_tool(tool_name)
            call_arguments = self._build_tool_arguments(tool, tool_name, arguments)
            self._logger.info("[DATA-FACTORY-MCP] Calling tool: %s", tool_name)
            ainvoke = getattr(tool, "ainvoke", None)
            result = (
                await ainvoke(call_arguments)
                if callable(ainvoke)
                else tool.invoke(call_arguments)
            )
            return self._coerce_tool_result(result)
        except Exception as exc:
            sub_errors = self._collect_sub_exceptions(exc)
            self._logger.error(
                "[DATA-FACTORY-MCP] Error calling %s: %s", tool_name, exc, exc_info=True
            )
            return {
                "error": f"{type(exc).__name__}: {exc}",
                "endpoint": self._settings_provider.get_data_factory_url(),
                "sub_exceptions": sub_errors,
            }
