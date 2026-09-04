from __future__ import annotations

import json
from dataclasses import dataclass

from smart_service_sdk.layer2_application.repositories.runtime_setting_repository_interface import (
    IRuntimeSettingRepository,
)


RUNTIME_SETTING_DEFAULTS: dict[str, str] = {
    "SEARCH_FUZZY_THRESHOLD": "0.8",
    "VECTOR_SEARCH_TOP_K": "10",
    "VECTOR_MIN_SCORE": "0.45",
    "DUPLICATE_GRAPH_SCORE_WEIGHT": "0.08",
    "SEARCH_MAX_RESULTS": "10",
    "SEARCH_EXPAND_TERMS_ENABLED": "false",
    "SIMILARITY_GRAPH_SEED_TOP_K": "8",
    "SIMILARITY_GRAPH_NEIGHBOR_CAP_PER_SEED": "6",
    "SIMILARITY_GRAPH_MAX_RELATION_CANDIDATES": "24",
    "SIMILARITY_GRAPH_MAX_GRAPH_CHUNK_CANDIDATES": "16",
    "SIMILARITY_MAX_RESULTS": "10",
    # SA-1451: user-configurable field selection for ingest. A JSON array of
    # source field (column) names. An empty list means "use every field" — the
    # backward-compatible default that keeps pre-SA-1451 behavior unchanged.
    "INGEST_EMBEDDING_FIELDS": "[]",
    "INGEST_GRAPH_ENTITY_FIELDS": "[]",
    # SA-1474 — material SDS source config (JSON-encoded lists; global scope).
    # Sensible defaults point the SDS check at authoritative hazard/SDS sources.
    "MATERIAL_SDS_RESOURCE_URLS": json.dumps(
        [
            "https://pubchem.ncbi.nlm.nih.gov",
            "https://echa.europa.eu",
            "https://www.osha.gov",
        ]
    ),
    "MATERIAL_SDS_ALLOWED_DOMAINS": json.dumps(
        [
            "pubchem.ncbi.nlm.nih.gov",
            "echa.europa.eu",
            "osha.gov",
        ]
    ),
}

FIELD_SELECTION_CONFIG_KEYS = (
    "INGEST_EMBEDDING_FIELDS",
    "INGEST_GRAPH_ENTITY_FIELDS",
)

SEARCH_CONFIG_KEYS = (
    "SEARCH_FUZZY_THRESHOLD",
    "SEARCH_MAX_RESULTS",
    "SEARCH_EXPAND_TERMS_ENABLED",
)

SIMILARITY_CONFIG_KEYS = (
    "VECTOR_SEARCH_TOP_K",
    "VECTOR_MIN_SCORE",
    "SIMILARITY_GRAPH_SEED_TOP_K",
    "SIMILARITY_GRAPH_NEIGHBOR_CAP_PER_SEED",
    "SIMILARITY_GRAPH_MAX_RELATION_CANDIDATES",
    "SIMILARITY_GRAPH_MAX_GRAPH_CHUNK_CANDIDATES",
    "SIMILARITY_MAX_RESULTS",
)

DUPLICATE_MATCH_CONFIG_KEYS = (
    "SEARCH_FUZZY_THRESHOLD",
    "VECTOR_SEARCH_TOP_K",
    "VECTOR_MIN_SCORE",
    "DUPLICATE_GRAPH_SCORE_WEIGHT",
)

MATERIAL_SDS_SOURCE_CONFIG_KEYS = (
    "MATERIAL_SDS_RESOURCE_URLS",
    "MATERIAL_SDS_ALLOWED_DOMAINS",
)


@dataclass(frozen=True)
class SearchRuntimeConfig:
    fuzzy_threshold: float
    max_results: int
    expand_terms_enabled: bool


@dataclass(frozen=True)
class SimilarityRuntimeConfig:
    vector_top_k: int
    vector_min_score: float
    graph_seed_top_k: int
    graph_neighbor_cap_per_seed: int
    graph_max_relation_candidates: int
    graph_max_graph_chunk_candidates: int
    max_results: int


@dataclass(frozen=True)
class DuplicateRuntimeConfig:
    fuzzy_threshold: float
    vector_search_top_k: int
    vector_min_score: float
    graph_score_weight: float


@dataclass(frozen=True)
class FieldSelectionConfig:
    """SA-1451 — which source fields feed each ingest artifact.

    An empty tuple means "use every field present on the row", preserving the
    pre-SA-1451 behavior. Order is significant: it is the order fields are
    concatenated into the embedded text.
    """

    embedding_fields: tuple[str, ...] = ()
    graph_entity_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaterialSdsSourceConfig:
    """SA-1474 — user-configurable sources for the material SDS requirement check.

    ``resource_urls`` are authoritative reference URLs surfaced to the model.
    ``allowed_domains`` restrict the online ``web_search`` lookup. Both are
    persisted as JSON-encoded lists under a single, global runtime-settings scope.
    """

    resource_urls: list[str]
    allowed_domains: list[str]


class RuntimeConfigurationError(ValueError):
    pass


class RuntimeConfigurationService:
    def __init__(self, repository: IRuntimeSettingRepository):
        self._repository = repository
        self._similarity_config_cache: SimilarityRuntimeConfig | None = None
        self._field_config_cache: FieldSelectionConfig | None = None

    async def get_search_config(self) -> SearchRuntimeConfig:
        values = await self._required_many(list(SEARCH_CONFIG_KEYS))
        return SearchRuntimeConfig(
            fuzzy_threshold=self._as_float(values, "SEARCH_FUZZY_THRESHOLD"),
            max_results=self._as_int(values, "SEARCH_MAX_RESULTS"),
            expand_terms_enabled=self._as_bool(values, "SEARCH_EXPAND_TERMS_ENABLED"),
        )

    async def update_search_config(self, config: SearchRuntimeConfig) -> SearchRuntimeConfig:
        await self._repository.set("SEARCH_FUZZY_THRESHOLD", str(config.fuzzy_threshold))
        await self._repository.set("SEARCH_MAX_RESULTS", str(config.max_results))
        await self._repository.set(
            "SEARCH_EXPAND_TERMS_ENABLED",
            "true" if config.expand_terms_enabled else "false",
        )
        return await self.get_search_config()

    async def get_similarity_config(self) -> SimilarityRuntimeConfig:
        if self._similarity_config_cache is not None:
            return self._similarity_config_cache
        values = await self._required_many(list(SIMILARITY_CONFIG_KEYS))
        config = SimilarityRuntimeConfig(
            vector_top_k=self._as_int(values, "VECTOR_SEARCH_TOP_K"),
            vector_min_score=self._as_float(values, "VECTOR_MIN_SCORE"),
            graph_seed_top_k=self._as_int(values, "SIMILARITY_GRAPH_SEED_TOP_K"),
            graph_neighbor_cap_per_seed=self._as_int(
                values, "SIMILARITY_GRAPH_NEIGHBOR_CAP_PER_SEED"
            ),
            graph_max_relation_candidates=self._as_int(
                values, "SIMILARITY_GRAPH_MAX_RELATION_CANDIDATES"
            ),
            graph_max_graph_chunk_candidates=self._as_int(
                values, "SIMILARITY_GRAPH_MAX_GRAPH_CHUNK_CANDIDATES"
            ),
            max_results=self._as_int(values, "SIMILARITY_MAX_RESULTS"),
        )
        self._similarity_config_cache = config
        return config

    async def update_similarity_config(
        self, config: SimilarityRuntimeConfig
    ) -> SimilarityRuntimeConfig:
        await self._repository.set("VECTOR_SEARCH_TOP_K", str(config.vector_top_k))
        await self._repository.set("VECTOR_MIN_SCORE", str(config.vector_min_score))
        await self._repository.set(
            "SIMILARITY_GRAPH_SEED_TOP_K", str(config.graph_seed_top_k)
        )
        await self._repository.set(
            "SIMILARITY_GRAPH_NEIGHBOR_CAP_PER_SEED",
            str(config.graph_neighbor_cap_per_seed),
        )
        await self._repository.set(
            "SIMILARITY_GRAPH_MAX_RELATION_CANDIDATES",
            str(config.graph_max_relation_candidates),
        )
        await self._repository.set(
            "SIMILARITY_GRAPH_MAX_GRAPH_CHUNK_CANDIDATES",
            str(config.graph_max_graph_chunk_candidates),
        )
        await self._repository.set("SIMILARITY_MAX_RESULTS", str(config.max_results))
        self._similarity_config_cache = None
        return await self.get_similarity_config()

    async def get_duplicate_config(self) -> DuplicateRuntimeConfig:
        values = await self._required_many(list(DUPLICATE_MATCH_CONFIG_KEYS))
        return DuplicateRuntimeConfig(
            fuzzy_threshold=self._as_float(values, "SEARCH_FUZZY_THRESHOLD"),
            vector_search_top_k=self._as_int(values, "VECTOR_SEARCH_TOP_K"),
            vector_min_score=self._as_float(values, "VECTOR_MIN_SCORE"),
            graph_score_weight=self._as_float(values, "DUPLICATE_GRAPH_SCORE_WEIGHT"),
        )

    async def get_field_config(self) -> FieldSelectionConfig:
        if self._field_config_cache is not None:
            return self._field_config_cache
        values = await self._repository.get_many(list(FIELD_SELECTION_CONFIG_KEYS))
        config = FieldSelectionConfig(
            embedding_fields=self._as_field_list(values, "INGEST_EMBEDDING_FIELDS"),
            graph_entity_fields=self._as_field_list(values, "INGEST_GRAPH_ENTITY_FIELDS"),
        )
        self._field_config_cache = config
        return config

    async def update_field_config(
        self, config: FieldSelectionConfig
    ) -> FieldSelectionConfig:
        await self._repository.set(
            "INGEST_EMBEDDING_FIELDS", json.dumps(list(config.embedding_fields))
        )
        await self._repository.set(
            "INGEST_GRAPH_ENTITY_FIELDS", json.dumps(list(config.graph_entity_fields))
        )
        # Invalidate so the next ingest reads the new selection. Bulk re-index of
        # already-indexed data is triggered separately (ReindexFilesUseCase).
        self._field_config_cache = None
        return await self.get_field_config()

    @staticmethod
    def _as_field_list(values: dict[str, str], key: str) -> tuple[str, ...]:
        raw = values.get(key)
        if raw is None or not str(raw).strip():
            return ()
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise RuntimeConfigurationError(
                f"Invalid JSON field list for runtime setting {key}"
            ) from exc
        if not isinstance(parsed, list):
            raise RuntimeConfigurationError(
                f"Runtime setting {key} must be a JSON array of field names"
            )
        return tuple(
            str(item).strip() for item in parsed if str(item).strip()
        )

    async def get_material_sds_source_config(self) -> MaterialSdsSourceConfig:
        # AC1: return defaults when unset. Unlike the scalar configs, this must not
        # raise on a fresh store — fall back to RUNTIME_SETTING_DEFAULTS per key so
        # the cold-start path (empty DB, no seed) still yields the documented defaults.
        values = await self._repository.get_many(list(MATERIAL_SDS_SOURCE_CONFIG_KEYS))
        return MaterialSdsSourceConfig(
            resource_urls=self._json_list_with_default(
                values, "MATERIAL_SDS_RESOURCE_URLS"
            ),
            allowed_domains=self._json_list_with_default(
                values, "MATERIAL_SDS_ALLOWED_DOMAINS"
            ),
        )

    async def update_material_sds_source_config(
        self, config: MaterialSdsSourceConfig
    ) -> MaterialSdsSourceConfig:
        await self._repository.set(
            "MATERIAL_SDS_RESOURCE_URLS", json.dumps(list(config.resource_urls))
        )
        await self._repository.set(
            "MATERIAL_SDS_ALLOWED_DOMAINS", json.dumps(list(config.allowed_domains))
        )
        return await self.get_material_sds_source_config()

    async def _required_many(self, keys: list[str]) -> dict[str, str]:
        values = await self._repository.get_many(keys)
        missing = [key for key in keys if not str(values.get(key, "")).strip()]
        if missing:
            raise RuntimeConfigurationError(
                f"Missing required runtime settings: {', '.join(sorted(missing))}"
            )
        return values

    @staticmethod
    def _as_str(values: dict[str, str], key: str) -> str:
        value = values.get(key)
        if value is None or not str(value).strip():
            raise RuntimeConfigurationError(f"Missing required runtime setting: {key}")
        return str(value)

    @staticmethod
    def _as_float(values: dict[str, str], key: str) -> float:
        try:
            return float(RuntimeConfigurationService._as_str(values, key))
        except ValueError as exc:
            raise RuntimeConfigurationError(
                f"Invalid float runtime setting for {key}"
            ) from exc

    @staticmethod
    def _as_int(values: dict[str, str], key: str) -> int:
        try:
            return int(RuntimeConfigurationService._as_str(values, key))
        except ValueError as exc:
            raise RuntimeConfigurationError(
                f"Invalid integer runtime setting for {key}"
            ) from exc

    @staticmethod
    def _as_bool(values: dict[str, str], key: str) -> bool:
        value = RuntimeConfigurationService._as_str(values, key).strip().lower()
        if value in {"true", "1", "yes", "on"}:
            return True
        if value in {"false", "0", "no", "off"}:
            return False
        raise RuntimeConfigurationError(f"Invalid boolean runtime setting for {key}")

    @staticmethod
    def _as_json_list(values: dict[str, str], key: str) -> list[str]:
        return RuntimeConfigurationService._parse_json_list(
            RuntimeConfigurationService._as_str(values, key), key
        )

    @classmethod
    def _json_list_with_default(cls, values: dict[str, str], key: str) -> list[str]:
        raw = values.get(key)
        if raw is None or not str(raw).strip():
            raw = RUNTIME_SETTING_DEFAULTS[key]
        return cls._parse_json_list(str(raw), key)

    @staticmethod
    def _parse_json_list(raw: str, key: str) -> list[str]:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeConfigurationError(
                f"Invalid JSON list runtime setting for {key}"
            ) from exc
        if not isinstance(parsed, list):
            raise RuntimeConfigurationError(
                f"Runtime setting {key} must be a JSON list"
            )
        return [str(item) for item in parsed]
