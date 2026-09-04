from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any

from smart_service_sdk.layer1_domain.entities.retrieval_candidate import RetrievalCandidate
from smart_service_sdk.layer1_domain.entities.retrieval_query import RetrievalRoute
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    RuntimeConfigurationService,
)
from smart_service_sdk.layer2_application.interfaces.embedding_provider_interface import IEmbeddingProvider
from smart_service_sdk.layer2_application.repositories.retrieval_chunk_repository_interface import (
    IRetrievalChunkRepository,
)
from smart_service_sdk.layer2_application.repositories.retrieval_graph_repository_interface import (
    IRetrievalGraphRepository,
)


@dataclass(frozen=True)
class SimilarityCommand:
    query_text: str
    mode: str = "ALL"
    tenant_id: str | None = None
    top_k: int | None = None
    filters: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SimilarityResult:
    tenant_id: str
    mode: str
    candidates: list[RetrievalCandidate]


class SimilarityUseCase:
    def __init__(
        self,
        *,
        logger,
        embedding_provider: IEmbeddingProvider,
        retrieval_chunk_repository: IRetrievalChunkRepository,
        retrieval_graph_repository: IRetrievalGraphRepository,
        runtime_configuration_service: RuntimeConfigurationService,
        query_entity_extractor,
        default_tenant_id: str,
    ):
        self._logger = logger
        self._embedding_provider = embedding_provider
        self._retrieval_chunk_repository = retrieval_chunk_repository
        self._retrieval_graph_repository = retrieval_graph_repository
        self._runtime_configuration_service = runtime_configuration_service
        self._query_entity_extractor = query_entity_extractor
        self._default_tenant_id = default_tenant_id

    async def execute(self, command: SimilarityCommand) -> SimilarityResult:
        tenant_id = command.tenant_id or self._default_tenant_id
        mode = str(command.mode or "ALL").strip().upper()
        _, config = await self._timed(
            "similarity_config",
            self._runtime_configuration_service.get_similarity_config,
        )
        _, query_embedding = await self._timed(
            "query_embedding",
            self._embedding_provider.embed_query,
            command.query_text,
        )
        final_top_k = max(command.top_k or config.max_results, 1)

        vector_candidates: list[RetrievalCandidate] = []
        graph_candidates: list[RetrievalCandidate] = []

        if mode in {"VECTOR", "ALL"}:
            _, vector_candidates = await self._timed(
                "vector_match",
                self._vector_matches,
                tenant_id=tenant_id,
                query_embedding=query_embedding,
                filters=command.filters,
                top_k=final_top_k,
                vector_top_k=config.vector_top_k,
                vector_min_score=config.vector_min_score,
            )

        if mode in {"GRAPH", "ALL"}:
            _, graph_candidates = await self._timed(
                "graph_match",
                self._graph_matches,
                query_text=command.query_text,
                tenant_id=tenant_id,
                query_embedding=query_embedding,
                filters=command.filters,
                config=config,
            )

        _, candidates = await self._timed(
            "candidate_merge",
            self._merge_candidates,
            vector_candidates=vector_candidates,
            graph_candidates=graph_candidates,
            mode=mode,
            top_k=final_top_k,
        )
        return SimilarityResult(tenant_id=tenant_id, mode=mode, candidates=candidates)

    async def _vector_matches(
        self,
        *,
        tenant_id: str,
        query_embedding: list[float],
        filters: dict[str, object],
        top_k: int,
        vector_top_k: int,
        vector_min_score: float,
    ) -> list[RetrievalCandidate]:
        candidates = await self._retrieval_chunk_repository.find_vector_candidates(
            tenant_id=tenant_id,
            query_embedding=query_embedding,
            top_k=max(top_k, vector_top_k),
            status="indexed",
            filters=filters,
        )
        return [
            candidate for candidate in candidates if candidate.score >= vector_min_score
        ]

    async def _graph_matches(
        self,
        *,
        query_text: str,
        tenant_id: str,
        query_embedding: list[float],
        filters: dict[str, object],
        config,
    ) -> list[RetrievalCandidate]:
        seed_texts = self._query_entity_extractor.extract(query_text.strip())
        if not seed_texts:
            return []
        return await self._retrieval_graph_repository.find_graph_candidates(
            tenant_id=tenant_id,
            query_embedding=query_embedding,
            seed_texts=seed_texts,
            filters=filters,
            seed_top_k=config.graph_seed_top_k,
            neighbor_cap_per_seed=config.graph_neighbor_cap_per_seed,
            max_relation_candidates=config.graph_max_relation_candidates,
            max_graph_chunk_candidates=config.graph_max_graph_chunk_candidates,
            max_hops=1,
        )

    @staticmethod
    def _merge_candidates(
        *,
        vector_candidates: list[RetrievalCandidate],
        graph_candidates: list[RetrievalCandidate],
        mode: str,
        top_k: int,
    ) -> list[RetrievalCandidate]:
        if mode == "VECTOR":
            return vector_candidates[:top_k]
        if mode == "GRAPH":
            return graph_candidates[:top_k]

        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for candidate in vector_candidates:
            key = (candidate.document_id, candidate.chunk_id)
            merged[key] = {
                "candidate": candidate,
                "vector_score": candidate.score,
                "graph_score": 0.0,
                "modes": {"VECTOR"},
            }
        for candidate in graph_candidates:
            key = (candidate.document_id, candidate.chunk_id)
            if key not in merged:
                merged[key] = {
                    "candidate": candidate,
                    "vector_score": 0.0,
                    "graph_score": candidate.score,
                    "modes": {"GRAPH"},
                }
                continue
            merged[key]["graph_score"] = max(
                float(merged[key]["graph_score"]),
                candidate.score,
            )
            merged[key]["modes"].add("GRAPH")

        materialized: list[RetrievalCandidate] = []
        for item in merged.values():
            candidate = item["candidate"]
            metadata = dict(candidate.metadata)
            metadata["vector_score"] = round(float(item["vector_score"]), 4)
            metadata["graph_score"] = round(float(item["graph_score"]), 4)
            metadata["contributing_modes"] = sorted(item["modes"])
            score = max(
                float(item["vector_score"]),
                float(item["graph_score"]),
            )
            materialized.append(
                RetrievalCandidate(
                    document_id=candidate.document_id,
                    chunk_id=candidate.chunk_id,
                    content=candidate.content,
                    score=score,
                    route=RetrievalRoute.HYBRID_DOCUMENT,
                    metadata=metadata,
                )
            )
        materialized.sort(key=lambda candidate: candidate.score, reverse=True)
        return materialized[:top_k]

    async def _timed(self, step_name: str, operation, *args, **kwargs):
        started_at = asyncio.get_running_loop().time()
        result = operation(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        elapsed_ms = round((asyncio.get_running_loop().time() - started_at) * 1000, 3)
        self._logger.info(
            "Similarity performance",
            step=step_name,
            duration_ms=elapsed_ms,
        )
        return elapsed_ms, result
