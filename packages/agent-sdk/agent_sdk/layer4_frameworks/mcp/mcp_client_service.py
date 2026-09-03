from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from typing import Any, cast

from langchain_core.tools import StructuredTool, ToolException
from pydantic import BaseModel, Field, create_model


class _LazyModule:
    def __init__(self, import_name: str) -> None:
        object.__setattr__(self, "_import_name", import_name)
        object.__setattr__(self, "_module", None)
        object.__setattr__(self, "_overrides", {})

    def _load(self) -> Any:
        module = object.__getattribute__(self, "_module")
        if module is None:
            import importlib

            import_name = object.__getattribute__(self, "_import_name")
            module = importlib.import_module(import_name)
            object.__setattr__(self, "_module", module)
        return module

    def __getattr__(self, name: str) -> Any:
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        return getattr(self._load(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_import_name", "_module", "_overrides"):
            object.__setattr__(self, name, value)
        else:
            overrides = object.__getattribute__(self, "_overrides")
            overrides[name] = value

    def __delattr__(self, name: str) -> None:
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            del overrides[name]
        else:
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )


shared_mcp_client_module = _LazyModule("simplemdg_mcp_client")


class MCPClientService:
    def __init__(
        self,
        server_configs: list[dict],
        logger: Any = None,
        discovery_timeout_seconds: float = 10.0,
    ) -> None:
        self._server_configs = server_configs
        self._logger = logger or logging.getLogger(__name__)
        self._discovery_timeout_seconds = discovery_timeout_seconds
        self._cached_tools: list[Any] | None = None
        self._call_session_managers: dict[str, Any] = {}
        self._call_sessions: dict[str, Any] = {}
        self._call_session_locks: dict[str, asyncio.Lock] = {}
        self._call_session_in_flight: dict[str, int] = {}
        self._call_session_idle_events: dict[str, asyncio.Event] = {}
        self._closing_call_sessions: set[str] = set()
        self.last_discovery_failures: list[str] = []

    def _build_client_config(self, server: dict[str, Any]) -> Any | None:
        server_url = str(server.get("server_url") or server.get("url") or "").rstrip(
            "/"
        )
        if not server_url:
            name = server.get("name", "unnamed")
            self._logger.warning("MCP server '%s' has no URL; skipping.", name)
            return None

        transport_value = str(server.get("transport", "streamable-http"))
        transport = shared_mcp_client_module.normalize_transport(transport_value)

        if server.get("server_url"):
            sse_path = str(server.get("sse_path", "/mcp/sse/sse"))
            streamable_http_path = str(
                server.get("streamable_http_path", "/mcp/http/mcp")
            )
        else:
            # Backward compatibility: legacy `url` values are exact endpoints.
            sse_path = ""
            streamable_http_path = ""

        return shared_mcp_client_module.MCPClientConfig(
            server_url=server_url,
            transport=transport,
            sse_path=sse_path,
            streamable_http_path=streamable_http_path,
        )

    def _build_tool_metadata(
        self, raw_tool: Any, server_name: str, raw_tool_name: str
    ) -> dict[str, Any] | None:
        annotations = getattr(raw_tool, "annotations", None)
        metadata = {}
        if annotations is not None:
            if hasattr(annotations, "model_dump"):
                metadata.update(annotations.model_dump())
            elif isinstance(annotations, dict):
                metadata.update(annotations)

        meta = getattr(raw_tool, "meta", None)
        if meta is not None:
            metadata["_meta"] = meta

        metadata["mcp_server_name"] = server_name
        metadata["mcp_raw_name"] = raw_tool_name

        return metadata or None

    def _convert_tool_content(self, content: Any) -> dict[str, Any]:
        content_type = getattr(content, "type", None)
        if content_type == "text":
            return {"type": "text", "text": getattr(content, "text", "")}
        return {"type": "text", "text": str(content)}

    def _convert_tool_result(
        self, result: Any
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        converted_content = [
            self._convert_tool_content(content)
            for content in getattr(result, "content", [])
        ]

        if getattr(result, "isError", False):
            error_text = "\n".join(
                block.get("text", "")
                for block in converted_content
                if isinstance(block, dict) and block.get("type") == "text"
            )
            raise ToolException(error_text or str(converted_content))

        structured_content = getattr(result, "structuredContent", None)
        artifact = None
        if structured_content is not None:
            artifact = {"structured_content": structured_content}

        return converted_content, artifact

    def _python_type_from_json_schema(self, schema: dict[str, Any]) -> Any:
        schema_type = schema.get("type")
        if isinstance(schema_type, list):
            non_null_types = [item for item in schema_type if item != "null"]
            base_type = (
                self._python_type_from_json_schema(
                    {**schema, "type": non_null_types[0]}
                )
                if len(non_null_types) == 1
                else Any
            )
            return base_type | None if "null" in schema_type else base_type

        if schema_type == "string":
            return str
        if schema_type == "integer":
            return int
        if schema_type == "number":
            return float
        if schema_type == "boolean":
            return bool
        if schema_type == "array":
            items = schema.get("items")
            item_type = (
                self._python_type_from_json_schema(items)
                if isinstance(items, dict)
                else Any
            )
            return list[item_type]
        if schema_type == "object":
            return dict[str, Any]
        return Any

    def _sanitize_model_name(self, value: str) -> str:
        parts = [part for part in re.split(r"[^0-9A-Za-z]+", value) if part]
        if not parts:
            return "McpTool"
        return "".join(part.capitalize() for part in parts)

    def _build_empty_args_schema(self, wrapped_tool_name: str) -> type[BaseModel]:
        model_name = f"{self._sanitize_model_name(wrapped_tool_name)}Args"
        return create_model(model_name, __base__=BaseModel)

    def _build_args_schema(
        self, raw_tool: Any, wrapped_tool_name: str
    ) -> type[BaseModel]:
        input_schema = getattr(raw_tool, "inputSchema", {}) or {}
        if not isinstance(input_schema, dict):
            return self._build_empty_args_schema(wrapped_tool_name)
        if input_schema.get("type") != "object":
            return self._build_empty_args_schema(wrapped_tool_name)

        properties = input_schema.get("properties", {})
        if not isinstance(properties, dict):
            return self._build_empty_args_schema(wrapped_tool_name)

        model_fields: dict[str, tuple[Any, Any]] = {}
        required_fields = set(input_schema.get("required", []))
        for field_name, field_schema in properties.items():
            if not isinstance(field_name, str) or not field_name.isidentifier():
                return self._build_empty_args_schema(wrapped_tool_name)
            if not isinstance(field_schema, dict):
                return self._build_empty_args_schema(wrapped_tool_name)

            annotation = self._python_type_from_json_schema(field_schema)
            default = (
                ...
                if field_name in required_fields
                else field_schema.get("default", None)
            )
            if field_name not in required_fields and default is None:
                annotation = annotation | None

            description = field_schema.get("description")
            model_fields[field_name] = (
                annotation,
                Field(default=default, description=description),
            )

        model_name = f"{self._sanitize_model_name(wrapped_tool_name)}Args"
        return create_model(
            model_name,
            __base__=BaseModel,
            **cast(dict[str, Any], model_fields),
        )

    def _get_call_session_lock(self, server_name: str) -> asyncio.Lock:
        return self._call_session_locks.setdefault(server_name, asyncio.Lock())

    def _get_call_session_idle_event(self, server_name: str) -> asyncio.Event:
        idle_event = self._call_session_idle_events.get(server_name)
        if idle_event is None:
            idle_event = asyncio.Event()
            idle_event.set()
            self._call_session_idle_events[server_name] = idle_event
        return idle_event

    def _qualify_tool_name(
        self, server_name: str, raw_tool_name: str, duplicate_count: int
    ) -> str:
        if duplicate_count <= 1:
            return raw_tool_name
        sanitized_server_name = re.sub(r"[^0-9A-Za-z_]+", "_", server_name).strip("_")
        if not sanitized_server_name:
            sanitized_server_name = "mcp"
        return f"{sanitized_server_name}_{raw_tool_name}"

    async def _get_or_open_call_session(self, server_name: str, config: Any) -> Any:
        session = self._call_sessions.get(server_name)
        if session is not None:
            return session

        manager = shared_mcp_client_module.open_mcp_session(config)
        try:
            session = await asyncio.wait_for(
                manager.__aenter__(), timeout=self._discovery_timeout_seconds
            )
        except BaseException:
            try:
                await manager.__aexit__(None, None, None)
            except Exception:
                self._logger.warning(
                    "Failed to unwind MCP session manager for '%s' after open error.",
                    server_name,
                    exc_info=True,
                )
            raise
        self._call_session_managers[server_name] = manager
        self._call_sessions[server_name] = session
        return session

    async def _close_call_session(self, server_name: str) -> None:
        self._call_sessions.pop(server_name, None)
        manager = self._call_session_managers.pop(server_name, None)
        if manager is None:
            return
        await manager.__aexit__(None, None, None)

    async def _acquire_call_session(self, server_name: str, config: Any) -> Any:
        lock = self._get_call_session_lock(server_name)
        idle_event = self._get_call_session_idle_event(server_name)
        async with lock:
            if server_name in self._closing_call_sessions:
                raise RuntimeError(f"MCP session for '{server_name}' is closing.")
            session = await self._get_or_open_call_session(server_name, config)
            self._call_session_in_flight[server_name] = (
                self._call_session_in_flight.get(server_name, 0) + 1
            )
            idle_event.clear()
            return session

    async def _release_call_session(self, server_name: str) -> None:
        lock = self._get_call_session_lock(server_name)
        idle_event = self._get_call_session_idle_event(server_name)
        async with lock:
            remaining_calls = self._call_session_in_flight.get(server_name, 0) - 1
            if remaining_calls <= 0:
                self._call_session_in_flight.pop(server_name, None)
                idle_event.set()
                return
            self._call_session_in_flight[server_name] = remaining_calls

    async def _invalidate_call_session(
        self, server_name: str, failed_session: Any
    ) -> None:
        lock = self._get_call_session_lock(server_name)
        async with lock:
            if self._call_sessions.get(server_name) is failed_session:
                await self._close_call_session(server_name)

    async def _discover_server_tools(
        self,
        server_name: str,
        config: Any,
    ) -> list[tuple[str, Any, Any]]:
        async def _connect_and_list() -> list[Any]:
            async with shared_mcp_client_module.open_mcp_session(config) as session:
                return await shared_mcp_client_module.list_tools(session)

        raw_tools = await asyncio.wait_for(
            _connect_and_list(),
            timeout=self._discovery_timeout_seconds,
        )
        self._logger.info(
            "MCP server '%s' is healthy: %s", server_name, config.endpoint
        )
        return [(server_name, config, raw_tool) for raw_tool in raw_tools]

    async def _invoke_tool(
        self,
        server_name: str,
        config: Any,
        raw_tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        session = await self._acquire_call_session(server_name, config)
        try:
            result = await shared_mcp_client_module.call_tool(
                session,
                raw_tool_name,
                arguments=arguments,
            )
        except Exception:
            await self._invalidate_call_session(server_name, session)
            raise
        finally:
            await self._release_call_session(server_name)
        return self._convert_tool_result(result)

    def _wrap_tool(
        self,
        raw_tool: Any,
        config: Any,
        server_name: str,
        wrapped_tool_name: str,
    ) -> StructuredTool:
        raw_tool_name = getattr(raw_tool, "name", "")
        metadata = self._build_tool_metadata(raw_tool, server_name, raw_tool_name)
        args_schema = self._build_args_schema(raw_tool, wrapped_tool_name)

        async def _invoke_tool(
            **arguments: Any,
        ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
            return await self._invoke_tool(
                server_name,
                config,
                raw_tool_name,
                arguments,
            )

        return StructuredTool(
            name=wrapped_tool_name,
            description=getattr(raw_tool, "description", "") or "",
            args_schema=args_schema,
            coroutine=_invoke_tool,
            handle_tool_error=True,
            response_format="content_and_artifact",
            metadata=metadata,
        )

    async def _discover_tools(self) -> list[Any]:
        if self._cached_tools is not None:
            return self._cached_tools

        discovered_entries: list[tuple[str, Any, Any]] = []
        failed_servers: list[str] = []
        servers_to_probe: list[tuple[str, Any]] = []
        for server in self._server_configs:
            name = str(server.get("name", "unnamed"))
            config = self._build_client_config(server)
            if config is None:
                continue

            servers_to_probe.append((name, config))

        probe_results = await asyncio.gather(
            *[
                self._discover_server_tools(name, config)
                for name, config in servers_to_probe
            ],
            return_exceptions=True,
        )
        for (name, _), result in zip(servers_to_probe, probe_results):
            if isinstance(result, BaseException):
                failed_servers.append(name)
                self._logger.warning(
                    "Failed to load MCP tools from '%s': %s", name, result
                )
                continue
            discovered_entries.extend(result)

        self.last_discovery_failures = failed_servers

        raw_name_counts = Counter(
            getattr(raw_tool, "name", "") for _, _, raw_tool in discovered_entries
        )
        discovered_tools = [
            self._wrap_tool(
                raw_tool,
                config,
                server_name,
                self._qualify_tool_name(
                    server_name,
                    getattr(raw_tool, "name", ""),
                    raw_name_counts.get(getattr(raw_tool, "name", ""), 0),
                ),
            )
            for server_name, config, raw_tool in discovered_entries
        ]

        if not discovered_tools and self._server_configs:
            self._logger.warning("No healthy MCP servers found.")
            return []

        if not failed_servers:
            self._cached_tools = discovered_tools
        return discovered_tools

    async def get_tools(self) -> list[Any]:
        if not self._server_configs:
            return []

        try:
            return await self._discover_tools()
        except Exception as exc:
            self._logger.error("Failed to get MCP tools: %s", exc)
            return []

    async def get_filtered_tools(self, tool_names: list[str]) -> list[Any]:
        all_tools = await self.get_tools()
        if not tool_names:
            return all_tools
        name_set = set(tool_names)
        return [
            tool
            for tool in all_tools
            if tool.name in name_set
            or ((getattr(tool, "metadata", None) or {}).get("mcp_raw_name") in name_set)
        ]

    async def close(self) -> None:
        server_names = set(self._call_session_managers) | set(self._call_session_locks)
        for server_name in server_names:
            idle_event = self._get_call_session_idle_event(server_name)
            self._closing_call_sessions.add(server_name)
            try:
                while True:
                    lock = self._get_call_session_lock(server_name)
                    async with lock:
                        if self._call_session_in_flight.get(server_name, 0) == 0:
                            await self._close_call_session(server_name)
                            break
                    await idle_event.wait()
            except Exception as exc:
                self._logger.warning(
                    "Failed to close MCP session for '%s': %s", server_name, exc
                )
            finally:
                self._closing_call_sessions.discard(server_name)
                idle_event.set()
        self._cached_tools = None
        self.last_discovery_failures = []
