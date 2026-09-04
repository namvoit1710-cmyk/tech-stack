import asyncio

import pytest

from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    FieldSelectionConfig,
    RuntimeConfigurationError,
    RuntimeConfigurationService,
)


class _FakeRuntimeSettingRepository:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)
        self.set_calls: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.set_calls.append((key, value))
        self.values[key] = value

    async def get_many(self, keys: list[str]) -> dict[str, str]:
        return {key: self.values[key] for key in keys if key in self.values}


def test_get_field_config_defaults_to_empty_selection() -> None:
    repository = _FakeRuntimeSettingRepository(
        {"INGEST_EMBEDDING_FIELDS": "[]", "INGEST_GRAPH_ENTITY_FIELDS": "[]"}
    )
    service = RuntimeConfigurationService(repository)

    config = asyncio.run(service.get_field_config())

    assert config == FieldSelectionConfig(embedding_fields=(), graph_entity_fields=())


def test_get_field_config_tolerates_missing_keys() -> None:
    # An older tenant with no field-config rows at all -> empty (use all fields).
    service = RuntimeConfigurationService(_FakeRuntimeSettingRepository({}))

    config = asyncio.run(service.get_field_config())

    assert config == FieldSelectionConfig()


def test_update_field_config_persists_json_and_refreshes_cache() -> None:
    repository = _FakeRuntimeSettingRepository(
        {"INGEST_EMBEDDING_FIELDS": "[]", "INGEST_GRAPH_ENTITY_FIELDS": "[]"}
    )
    service = RuntimeConfigurationService(repository)

    asyncio.run(service.get_field_config())  # prime the cache
    updated = asyncio.run(
        service.update_field_config(
            FieldSelectionConfig(
                embedding_fields=("Material Name", "Manufacturer"),
                graph_entity_fields=("Manufacturer",),
            )
        )
    )
    cached = asyncio.run(service.get_field_config())

    assert updated.embedding_fields == ("Material Name", "Manufacturer")
    assert updated.graph_entity_fields == ("Manufacturer",)
    assert repository.values["INGEST_EMBEDDING_FIELDS"] == '["Material Name", "Manufacturer"]'
    assert repository.values["INGEST_GRAPH_ENTITY_FIELDS"] == '["Manufacturer"]'
    # cache was invalidated on update and reloaded to the fresh values
    assert cached == updated


def test_invalid_json_field_list_raises() -> None:
    repository = _FakeRuntimeSettingRepository(
        {"INGEST_EMBEDDING_FIELDS": "not-json", "INGEST_GRAPH_ENTITY_FIELDS": "[]"}
    )
    service = RuntimeConfigurationService(repository)

    with pytest.raises(RuntimeConfigurationError):
        asyncio.run(service.get_field_config())
