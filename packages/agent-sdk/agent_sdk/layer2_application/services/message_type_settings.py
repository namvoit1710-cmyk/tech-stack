from __future__ import annotations

from typing import Any, Mapping

from agent_sdk.layer1_domain.value_objects.message_type_set import MessageTypeSet

_RESPONSE_MESSAGE_TYPE_SETTING_NAMES = (
    "QUEUE_RESPONSE_MESSAGE_TYPE",
    "DELEGATION_RESPONSE_MESSAGE_TYPE",
    "QUEUE_RESPONSE_MESSAGE_TYPES",
    "DELEGATION_RESPONSE_MESSAGE_TYPES",
)


def configured_response_message_types(
    settings: Any,
    *,
    default: str = "agent.response",
) -> set[str]:
    response_types = {default} if default else set()
    if settings is None:
        return response_types

    for name in _RESPONSE_MESSAGE_TYPE_SETTING_NAMES:
        response_types.update(MessageTypeSet.coerce(getattr(settings, name, "")))
    return response_types


def configured_response_message_types_from_dependencies(
    dependencies: Mapping[str, Any],
    *,
    default: str = "agent.response",
) -> set[str]:
    return configured_response_message_types(
        dependencies.get("settings"),
        default=default,
    )
