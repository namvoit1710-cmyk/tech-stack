import asyncio
import json

import pytest

from smart_service_sdk.layer2_application.features.material_sds_analysis.use_cases.material_sds_config_usecases import (
    GetMaterialSdsSourceConfigUseCase,
    MaterialSdsSourceConfigValidationError,
    UpdateMaterialSdsSourceConfigCommand,
    UpdateMaterialSdsSourceConfigUseCase,
)
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    RUNTIME_SETTING_DEFAULTS,
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


def _service(values: dict[str, str] | None = None) -> RuntimeConfigurationService:
    return RuntimeConfigurationService(
        _FakeRuntimeSettingRepository(values or dict(RUNTIME_SETTING_DEFAULTS))
    )


def test_get_returns_defaults_when_unset() -> None:
    config = asyncio.run(GetMaterialSdsSourceConfigUseCase(_service()).execute())

    assert "osha.gov" in config.allowed_domains
    assert all(url.startswith("https://") for url in config.resource_urls)
    assert config.resource_urls  # non-empty defaults


def test_get_returns_defaults_when_store_is_empty() -> None:
    # Cold-start / fresh-deploy path: the store has NO keys (not pre-seeded).
    # AC1 requires defaults, not an error. Regression guard for the seed/fallback bug.
    service = RuntimeConfigurationService(_FakeRuntimeSettingRepository({}))

    config = asyncio.run(GetMaterialSdsSourceConfigUseCase(service).execute())

    assert config.resource_urls == json.loads(
        RUNTIME_SETTING_DEFAULTS["MATERIAL_SDS_RESOURCE_URLS"]
    )
    assert config.allowed_domains == json.loads(
        RUNTIME_SETTING_DEFAULTS["MATERIAL_SDS_ALLOWED_DOMAINS"]
    )


def test_persisted_value_overrides_default_on_partial_store() -> None:
    # One key set, the other unset -> set key honored, unset key falls back.
    service = RuntimeConfigurationService(
        _FakeRuntimeSettingRepository(
            {"MATERIAL_SDS_ALLOWED_DOMAINS": json.dumps(["only.example.com"])}
        )
    )

    config = asyncio.run(GetMaterialSdsSourceConfigUseCase(service).execute())

    assert config.allowed_domains == ["only.example.com"]
    assert config.resource_urls == json.loads(
        RUNTIME_SETTING_DEFAULTS["MATERIAL_SDS_RESOURCE_URLS"]
    )


def test_update_round_trip_dedupes_and_persists_json() -> None:
    repository = _FakeRuntimeSettingRepository(dict(RUNTIME_SETTING_DEFAULTS))
    service = RuntimeConfigurationService(repository)

    config = asyncio.run(
        UpdateMaterialSdsSourceConfigUseCase(service).execute(
            UpdateMaterialSdsSourceConfigCommand(
                resource_urls=[
                    "https://example.com/sds",
                    "https://example.com/sds",  # duplicate
                ],
                allowed_domains=["Example.com", "example.com"],  # dup + case
            )
        )
    )

    assert config.resource_urls == ["https://example.com/sds"]
    assert config.allowed_domains == ["example.com"]
    assert json.loads(repository.values["MATERIAL_SDS_RESOURCE_URLS"]) == [
        "https://example.com/sds"
    ]
    assert json.loads(repository.values["MATERIAL_SDS_ALLOWED_DOMAINS"]) == [
        "example.com"
    ]


def test_update_rejects_non_https_resource_url() -> None:
    with pytest.raises(MaterialSdsSourceConfigValidationError):
        asyncio.run(
            UpdateMaterialSdsSourceConfigUseCase(_service()).execute(
                UpdateMaterialSdsSourceConfigCommand(
                    resource_urls=["http://insecure.example.com"],
                    allowed_domains=[],
                )
            )
        )


def test_update_rejects_domain_that_is_actually_a_url() -> None:
    with pytest.raises(MaterialSdsSourceConfigValidationError):
        asyncio.run(
            UpdateMaterialSdsSourceConfigUseCase(_service()).execute(
                UpdateMaterialSdsSourceConfigCommand(
                    resource_urls=[],
                    allowed_domains=["https://osha.gov/path"],
                )
            )
        )


def test_update_rejects_too_many_domains() -> None:
    with pytest.raises(MaterialSdsSourceConfigValidationError):
        asyncio.run(
            UpdateMaterialSdsSourceConfigUseCase(_service()).execute(
                UpdateMaterialSdsSourceConfigCommand(
                    resource_urls=[],
                    allowed_domains=[f"host{index}.example.com" for index in range(51)],
                )
            )
        )
