from __future__ import annotations

from typing import Any

from langchain_core.tools import tool as langchain_tool

_REGISTRY_HINTS_KEY = "agent_sdk_registry"


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _extract_registry_hints(kwargs: dict[str, Any]) -> dict[str, Any]:
    registry_hints: dict[str, Any] = {}

    internal = kwargs.pop("internal", None)
    if internal is not None:
        registry_hints["internal"] = _coerce_bool(internal)

    registry_name = kwargs.pop("registry_name", None)
    if registry_name is not None:
        registry_hints["registry_name"] = registry_name

    registry_owner = kwargs.pop("registry_owner", None)
    if registry_owner is not None:
        registry_hints["registry_owner"] = registry_owner

    registry_version = kwargs.pop("registry_version", None)
    if registry_version is not None:
        registry_hints["registry_version"] = registry_version

    registry_metadata = kwargs.pop("registry_metadata", None)
    if registry_metadata is not None:
        registry_hints["metadata"] = registry_metadata

    return registry_hints


def _attach_registry_hints(tool_obj: Any, registry_hints: dict[str, Any]) -> Any:
    if not registry_hints:
        return tool_obj

    existing_metadata = getattr(tool_obj, "metadata", None)
    metadata = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
    existing_hints = metadata.get(_REGISTRY_HINTS_KEY)
    merged_hints = dict(existing_hints) if isinstance(existing_hints, dict) else {}
    merged_hints.update(registry_hints)
    metadata[_REGISTRY_HINTS_KEY] = merged_hints
    tool_obj.metadata = metadata
    return tool_obj


def tool(*args: Any, **kwargs: Any) -> Any:
    registry_hints = _extract_registry_hints(kwargs)
    decorated = langchain_tool(*args, **kwargs)

    if args and callable(args[0]):
        return _attach_registry_hints(decorated, registry_hints)

    def _decorate(func: Any) -> Any:
        return _attach_registry_hints(decorated(func), registry_hints)

    return _decorate
