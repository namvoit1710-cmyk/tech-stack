import asyncio

import pytest

from smart_service_sdk.layer2_application.features.field_configuration.use_cases.field_config_usecases import (
    GetFieldConfigUseCase,
    UpdateFieldConfigCommand,
    UpdateFieldConfigUseCase,
)
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    FieldSelectionConfig,
    RuntimeConfigurationError,
    RuntimeConfigurationService,
)


class _FakeRuntimeSettingRepository:
    def __init__(self) -> None:
        self.values = {"INGEST_EMBEDDING_FIELDS": "[]", "INGEST_GRAPH_ENTITY_FIELDS": "[]"}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def get_many(self, keys: list[str]) -> dict[str, str]:
        return {key: self.values[key] for key in keys if key in self.values}


def _service() -> RuntimeConfigurationService:
    return RuntimeConfigurationService(_FakeRuntimeSettingRepository())


def test_update_strips_blanks_and_dedupes_preserving_order() -> None:
    use_case = UpdateFieldConfigUseCase(_service())

    result = asyncio.run(
        use_case.execute(
            UpdateFieldConfigCommand(
                embedding_fields=[" Material Name ", "Material Name", "", "Manufacturer"],
                graph_entity_fields=["Manufacturer", "Manufacturer"],
            )
        )
    )

    assert result == FieldSelectionConfig(
        embedding_fields=("Material Name", "Manufacturer"),
        graph_entity_fields=("Manufacturer",),
    )


def test_update_rejects_fields_outside_available_columns() -> None:
    use_case = UpdateFieldConfigUseCase(_service())

    with pytest.raises(RuntimeConfigurationError) as excinfo:
        asyncio.run(
            use_case.execute(
                UpdateFieldConfigCommand(
                    embedding_fields=["Material Name", "Nonexistent Column"],
                    graph_entity_fields=[],
                    available_fields=["Material Name", "Manufacturer"],
                )
            )
        )

    assert "Nonexistent Column" in str(excinfo.value)


def test_get_returns_current_config() -> None:
    service = _service()
    asyncio.run(
        UpdateFieldConfigUseCase(service).execute(
            UpdateFieldConfigCommand(
                embedding_fields=["Material Name"],
                graph_entity_fields=[],
            )
        )
    )

    result = asyncio.run(GetFieldConfigUseCase(service).execute())

    assert result.embedding_fields == ("Material Name",)
    assert result.graph_entity_fields == ()
