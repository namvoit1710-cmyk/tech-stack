from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from smart_service_sdk.layer2_application.features.field_configuration.field_selection import (
    unknown_fields,
)
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    FieldSelectionConfig,
    RuntimeConfigurationError,
    RuntimeConfigurationService,
)


def _clean(names: Sequence[str]) -> tuple[str, ...]:
    """Strip, drop blanks, and dedupe preserving first-seen order."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in names:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
    return tuple(cleaned)


@dataclass(frozen=True)
class UpdateFieldConfigCommand:
    embedding_fields: Sequence[str]
    graph_entity_fields: Sequence[str]
    # Optional candidate headers (e.g. from an uploaded file). When provided,
    # any selected field outside this set is rejected with a clear error.
    available_fields: Sequence[str] = field(default_factory=tuple)


class GetFieldConfigUseCase:
    def __init__(self, runtime_configuration_service: RuntimeConfigurationService):
        self._runtime_configuration_service = runtime_configuration_service

    async def execute(self) -> FieldSelectionConfig:
        return await self._runtime_configuration_service.get_field_config()


class UpdateFieldConfigUseCase:
    def __init__(self, runtime_configuration_service: RuntimeConfigurationService):
        self._runtime_configuration_service = runtime_configuration_service

    async def execute(
        self, command: UpdateFieldConfigCommand
    ) -> FieldSelectionConfig:
        embedding_fields = _clean(command.embedding_fields)
        graph_entity_fields = _clean(command.graph_entity_fields)

        if command.available_fields:
            missing = unknown_fields(
                [*embedding_fields, *graph_entity_fields],
                command.available_fields,
            )
            if missing:
                raise RuntimeConfigurationError(
                    "Unknown field(s) not present in the available columns: "
                    + ", ".join(missing)
                )

        return await self._runtime_configuration_service.update_field_config(
            FieldSelectionConfig(
                embedding_fields=embedding_fields,
                graph_entity_fields=graph_entity_fields,
            )
        )
