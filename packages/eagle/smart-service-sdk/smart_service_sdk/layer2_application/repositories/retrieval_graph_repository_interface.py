from typing import Protocol

from smart_service_sdk.layer1_domain.entities.retrieval_candidate import RetrievalCandidate
from smart_service_sdk.layer2_application.interfaces.graph_extractor_interface import (
    GraphExtractionResult,
)


class IRetrievalGraphRepository(Protocol):
    async def replace_file_graphs(
        self,
        *,
        tenant_id: str,
        file_id: str,
        document_graphs: list[tuple[str, GraphExtractionResult]],
    ) -> None: ...

    async def find_graph_candidates(
        self,
        *,
        tenant_id: str,
        query_embedding: list[float],
        seed_texts: list[str],
        filters: dict[str, object] | None,
        seed_top_k: int,
        neighbor_cap_per_seed: int,
        max_relation_candidates: int,
        max_graph_chunk_candidates: int,
        max_hops: int,
    ) -> list[RetrievalCandidate]: ...
