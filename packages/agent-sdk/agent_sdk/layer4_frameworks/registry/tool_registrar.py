from __future__ import annotations

import hashlib
import json
from typing import Any

from agent_sdk.layer1_domain.entities.tool_registration import ToolRegistration
from agent_sdk.layer2_application.interfaces.tool_registry import IToolRegistry

_REGISTRY_HINTS_KEY = "agent_sdk_registry"
_DEFAULT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}
_DEFINITION_HASH_ALGORITHM = "sha256:v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _definition_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _tool_registry_hints(tool: Any) -> dict[str, Any]:
    base_metadata = getattr(tool, "metadata", None)
    if not isinstance(base_metadata, dict):
        return {}
    registry_hints = base_metadata.get(_REGISTRY_HINTS_KEY)
    return dict(registry_hints) if isinstance(registry_hints, dict) else {}


def _is_internal_tool(tool: Any) -> bool:
    registry_hints = _tool_registry_hints(tool)
    return _coerce_bool(registry_hints.get("internal", False))


def _tool_to_parameters_schema(tool: Any) -> dict[str, Any]:
    schema_factory = getattr(tool, "get_input_schema", None)
    if callable(schema_factory):
        schema = schema_factory()
        model_json_schema = getattr(schema, "model_json_schema", None)
        if callable(model_json_schema):
            return model_json_schema()
        if isinstance(schema, dict):
            return schema

    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None:
        model_json_schema = getattr(args_schema, "model_json_schema", None)
        if callable(model_json_schema):
            return model_json_schema()

    return dict(_DEFAULT_SCHEMA)


def _tool_to_response_schema(tool: Any) -> dict[str, Any]:
    schema_factory = getattr(tool, "get_output_schema", None)
    if callable(schema_factory):
        schema = schema_factory()
        model_json_schema = getattr(schema, "model_json_schema", None)
        if callable(model_json_schema):
            return model_json_schema()
        if isinstance(schema, dict):
            return schema

    base_metadata = getattr(tool, "metadata", None)
    if isinstance(base_metadata, dict):
        for key in ("response_schema", "output_schema"):
            schema = base_metadata.get(key)
            if isinstance(schema, dict):
                return schema

    return dict(_DEFAULT_SCHEMA)


def _build_logical_registry_name(
    owner_kind: str,
    owner_name: str,
    tool_name: str,
) -> str:
    return f"{owner_kind}__{owner_name}__{tool_name}"


def _build_registry_name(
    owner_kind: str,
    owner_name: str,
    tool_name: str,
    owner_version: str,
) -> str:
    return f"{_build_logical_registry_name(owner_kind, owner_name, tool_name)}__{owner_version}"


def _convert_tool_to_registration(
    tool: Any,
    owner_kind: str,
    owner_name: str,
    owner_version: str,
) -> ToolRegistration:
    tool_name = getattr(tool, "name", "")
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("Local tools must expose a non-empty name")

    description = getattr(tool, "description", "")
    if not isinstance(description, str) or not description:
        description = f"Tool registered for {owner_name}"

    base_metadata = getattr(tool, "metadata", None)
    metadata = dict(base_metadata) if isinstance(base_metadata, dict) else {}
    registry_hints = metadata.pop(_REGISTRY_HINTS_KEY, None)
    if not isinstance(registry_hints, dict):
        registry_hints = {}

    effective_owner_kind = owner_kind
    effective_owner_name = owner_name
    owner_override = registry_hints.get("registry_owner")
    if isinstance(owner_override, dict):
        override_kind = owner_override.get("kind")
        override_name = owner_override.get("name")
        if isinstance(override_kind, str) and override_kind:
            effective_owner_kind = override_kind
        if isinstance(override_name, str) and override_name:
            effective_owner_name = override_name

    effective_version = owner_version
    version_override = registry_hints.get("registry_version")
    if isinstance(version_override, str) and version_override:
        effective_version = version_override

    hint_metadata = registry_hints.get("metadata")
    if isinstance(hint_metadata, dict):
        metadata.update(hint_metadata)

    logical_registry_name = registry_hints.get("logical_registry_name")
    if not isinstance(logical_registry_name, str) or not logical_registry_name:
        logical_registry_name = _build_logical_registry_name(
            effective_owner_kind,
            effective_owner_name,
            tool_name,
        )

    registry_name = registry_hints.get("registry_name")
    if not isinstance(registry_name, str) or not registry_name:
        registry_name = _build_registry_name(
            effective_owner_kind,
            effective_owner_name,
            tool_name,
            effective_version,
        )

    parameters_schema = _tool_to_parameters_schema(tool)
    response_schema = _tool_to_response_schema(tool)
    tool_definition_hash = _definition_hash(
        {
            "tool_name": tool_name,
            "description": description,
            "parameters_schema": parameters_schema,
            "response_schema": response_schema,
        }
    )

    metadata.update(
        {
            "inline": True,
            "langchain_tool": True,
            "sdk_owned": True,
            "owner_kind": effective_owner_kind,
            "owner_name": effective_owner_name,
            "owner_version": effective_version,
            "tool_name": tool_name,
            "logical_registry_name": logical_registry_name,
            "definition_hash": tool_definition_hash,
            "definition_hash_algorithm": _DEFINITION_HASH_ALGORITHM,
        }
    )

    return ToolRegistration(
        tool_name=tool_name,
        registry_name=registry_name,
        logical_registry_name=logical_registry_name,
        description=description,
        version=effective_version,
        parameters_schema=parameters_schema,
        response_schema=response_schema,
        metadata=metadata,
    )


class ToolRegistrar:
    def __init__(self, tool_registry: IToolRegistry) -> None:
        self._tool_registry = tool_registry

    async def register_tools(
        self,
        tools: list[Any],
        owner_kind: str,
        owner_name: str,
        owner_version: str,
    ) -> dict[str, str]:
        public_tools = [tool for tool in tools if not _is_internal_tool(tool)]
        registrations = [
            _convert_tool_to_registration(
                tool,
                owner_kind=owner_kind,
                owner_name=owner_name,
                owner_version=owner_version,
            )
            for tool in public_tools
        ]
        return await self._tool_registry.sync_tools(registrations)
