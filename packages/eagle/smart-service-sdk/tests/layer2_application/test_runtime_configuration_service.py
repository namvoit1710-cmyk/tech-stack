import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "smart_service_sdk" not in sys.modules:
    package = types.ModuleType("smart_service_sdk")
    package.__path__ = [str(ROOT / "smart_service_sdk")]
    sys.modules["smart_service_sdk"] = package

from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    RuntimeConfigurationService,
    SimilarityRuntimeConfig,
)


class _FakeRuntimeSettingRepository:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)
        self.get_many_calls: list[list[str]] = []
        self.set_calls: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.set_calls.append((key, value))
        self.values[key] = value

    async def get_many(self, keys: list[str]) -> dict[str, str]:
        self.get_many_calls.append(list(keys))
        return {key: self.values[key] for key in keys}


def _similarity_values() -> dict[str, str]:
    return {
        "VECTOR_SEARCH_TOP_K": "10",
        "VECTOR_MIN_SCORE": "0.45",
        "SIMILARITY_GRAPH_SEED_TOP_K": "8",
        "SIMILARITY_GRAPH_NEIGHBOR_CAP_PER_SEED": "6",
        "SIMILARITY_GRAPH_MAX_RELATION_CANDIDATES": "24",
        "SIMILARITY_GRAPH_MAX_GRAPH_CHUNK_CANDIDATES": "16",
        "SIMILARITY_MAX_RESULTS": "10",
    }


def test_get_similarity_config_uses_in_memory_cache_after_first_load() -> None:
    repository = _FakeRuntimeSettingRepository(_similarity_values())
    service = RuntimeConfigurationService(repository)

    first = asyncio.run(service.get_similarity_config())
    second = asyncio.run(service.get_similarity_config())

    assert first == SimilarityRuntimeConfig(
        vector_top_k=10,
        vector_min_score=0.45,
        graph_seed_top_k=8,
        graph_neighbor_cap_per_seed=6,
        graph_max_relation_candidates=24,
        graph_max_graph_chunk_candidates=16,
        max_results=10,
    )
    assert second == first
    assert len(repository.get_many_calls) == 1


def test_update_similarity_config_invalidates_cache_and_returns_fresh_values() -> None:
    repository = _FakeRuntimeSettingRepository(_similarity_values())
    service = RuntimeConfigurationService(repository)

    original = asyncio.run(service.get_similarity_config())
    updated = asyncio.run(
        service.update_similarity_config(
            SimilarityRuntimeConfig(
                vector_top_k=12,
                vector_min_score=0.55,
                graph_seed_top_k=9,
                graph_neighbor_cap_per_seed=7,
                graph_max_relation_candidates=30,
                graph_max_graph_chunk_candidates=18,
                max_results=14,
            )
        )
    )
    cached_after_update = asyncio.run(service.get_similarity_config())

    assert original.vector_top_k == 10
    assert updated == SimilarityRuntimeConfig(
        vector_top_k=12,
        vector_min_score=0.55,
        graph_seed_top_k=9,
        graph_neighbor_cap_per_seed=7,
        graph_max_relation_candidates=30,
        graph_max_graph_chunk_candidates=18,
        max_results=14,
    )
    assert cached_after_update == updated
    assert len(repository.get_many_calls) == 2
    assert repository.set_calls == [
        ("VECTOR_SEARCH_TOP_K", "12"),
        ("VECTOR_MIN_SCORE", "0.55"),
        ("SIMILARITY_GRAPH_SEED_TOP_K", "9"),
        ("SIMILARITY_GRAPH_NEIGHBOR_CAP_PER_SEED", "7"),
        ("SIMILARITY_GRAPH_MAX_RELATION_CANDIDATES", "30"),
        ("SIMILARITY_GRAPH_MAX_GRAPH_CHUNK_CANDIDATES", "18"),
        ("SIMILARITY_MAX_RESULTS", "14"),
    ]
