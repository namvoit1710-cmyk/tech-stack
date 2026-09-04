from __future__ import annotations

from dataclasses import dataclass

from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    RuntimeConfigurationService,
    SimilarityRuntimeConfig,
)


@dataclass(frozen=True)
class UpdateSimilarityConfigCommand:
    vector_top_k: int
    vector_min_score: float
    graph_seed_top_k: int
    graph_neighbor_cap_per_seed: int
    graph_max_relation_candidates: int
    graph_max_graph_chunk_candidates: int
    max_results: int


class GetSimilarityConfigUseCase:
    def __init__(self, runtime_configuration_service: RuntimeConfigurationService):
        self._runtime_configuration_service = runtime_configuration_service

    async def execute(self) -> SimilarityRuntimeConfig:
        return await self._runtime_configuration_service.get_similarity_config()


class UpdateSimilarityConfigUseCase:
    def __init__(self, runtime_configuration_service: RuntimeConfigurationService):
        self._runtime_configuration_service = runtime_configuration_service

    async def execute(
        self, command: UpdateSimilarityConfigCommand
    ) -> SimilarityRuntimeConfig:
        return await self._runtime_configuration_service.update_similarity_config(
            SimilarityRuntimeConfig(
                vector_top_k=command.vector_top_k,
                vector_min_score=command.vector_min_score,
                graph_seed_top_k=command.graph_seed_top_k,
                graph_neighbor_cap_per_seed=command.graph_neighbor_cap_per_seed,
                graph_max_relation_candidates=command.graph_max_relation_candidates,
                graph_max_graph_chunk_candidates=command.graph_max_graph_chunk_candidates,
                max_results=command.max_results,
            )
        )
