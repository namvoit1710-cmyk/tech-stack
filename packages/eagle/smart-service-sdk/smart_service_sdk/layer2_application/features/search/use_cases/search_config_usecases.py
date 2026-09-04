from __future__ import annotations

from dataclasses import dataclass

from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    RuntimeConfigurationService,
    SearchRuntimeConfig,
)


@dataclass(frozen=True)
class UpdateSearchConfigCommand:
    fuzzy_threshold: float
    max_results: int
    expand_terms_enabled: bool


class GetSearchConfigUseCase:
    def __init__(self, runtime_configuration_service: RuntimeConfigurationService):
        self._runtime_configuration_service = runtime_configuration_service

    async def execute(self) -> SearchRuntimeConfig:
        return await self._runtime_configuration_service.get_search_config()


class UpdateSearchConfigUseCase:
    def __init__(self, runtime_configuration_service: RuntimeConfigurationService):
        self._runtime_configuration_service = runtime_configuration_service

    async def execute(self, command: UpdateSearchConfigCommand) -> SearchRuntimeConfig:
        return await self._runtime_configuration_service.update_search_config(
            SearchRuntimeConfig(
                fuzzy_threshold=command.fuzzy_threshold,
                max_results=command.max_results,
                expand_terms_enabled=command.expand_terms_enabled,
            )
        )
