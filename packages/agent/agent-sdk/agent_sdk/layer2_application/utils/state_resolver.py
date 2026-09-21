from __future__ import annotations

from typing import Any, Mapping

from agent_sdk.layer1_domain.entities.conversation_metadata import ConversationMetadata
from agent_sdk.layer1_domain.entities.tenant_context import TenantContext
from agent_sdk.layer2_application.utils.definition import ensure_definition


def _build_conversation_metadata(
    value: Mapping[str, Any] | None,
) -> ConversationMetadata:
    payload = dict(value or {})
    return ConversationMetadata(
        main_conv_id=str(payload.get("main_conv_id") or ""),
        sub_conv_ids=list(payload.get("sub_conv_ids") or []),
        uploaded_file_ids=list(payload.get("uploaded_file_ids") or []),
    )


def _build_tenant_context(value: Mapping[str, Any] | None) -> TenantContext:
    payload = dict(value or {})
    return TenantContext(
        tenant_id=str(payload.get("tenant_id") or ""),
        user_id=str(payload.get("user_id") or ""),
        conv_id=str(payload.get("conv_id") or ""),
        source=str(payload.get("source") or "api"),
        correlation_id=payload.get("correlation_id"),
        metadata=dict(payload.get("metadata") or {}),
    )


class StateResolver:
    def __init__(self, value: Mapping[str, Any] | None = None):
        self._value = dict(value or {})

    @property
    def metadata(self) -> ConversationMetadata | None:
        if "metadata" not in self._value:
            return None
        return ensure_definition(
            self._value.get("metadata"),
            definition_type=ConversationMetadata,
            factory=_build_conversation_metadata,
        )

    @property
    def tenant_context(self) -> TenantContext | None:
        if "tenant_context" not in self._value:
            return None
        return ensure_definition(
            self._value.get("tenant_context"),
            definition_type=TenantContext,
            factory=_build_tenant_context,
        )

    @property
    def shared_state(self) -> dict[str, Any] | None:
        value = self._value.get("shared_state")
        if value is None:
            return None
        return dict(value)

    @property
    def shared_state_key(self) -> str | None:
        return self._value.get("shared_state_key")

    @property
    def shared_state_version(self) -> int | None:
        return self._value.get("shared_state_version")

    @property
    def execution_context(self) -> dict[str, Any] | None:
        value = self._value.get("execution_context")
        if value is None:
            return None
        return dict(value)

    @property
    def context_snapshot(self) -> dict[str, Any] | None:
        value = self._value.get("context_snapshot")
        if value is None:
            return None
        return dict(value)

    @property
    def uploaded_file_ids(self) -> list[str] | None:
        value = self._value.get("uploaded_file_ids")
        if value is None:
            return None
        return list(value)

    def get(self, name: str, default: Any = None) -> Any:
        return self._value.get(name, default)

    def __getattr__(self, name: str) -> Any:
        if name in self._value:
            return self._value[name]
        return None


def ensure_state_resolver(
    value: Mapping[str, Any] | StateResolver | None,
) -> StateResolver:
    return ensure_definition(
        value,
        definition_type=StateResolver,
        factory=StateResolver,
    )
