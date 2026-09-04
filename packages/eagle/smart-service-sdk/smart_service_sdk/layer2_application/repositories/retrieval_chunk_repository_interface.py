from typing import Protocol

from smart_service_sdk.layer1_domain.entities.duplicate_match_rule import DuplicateMatchRule
from smart_service_sdk.layer1_domain.entities.retrieval_candidate import RetrievalCandidate


class IRetrievalChunkRepository(Protocol):
    async def replace_file_chunks(
        self,
        tenant_id: str,
        file_id: str,
        documents: list[dict[str, object]],
        chunks: list[dict[str, object]],
    ) -> None: ...

    async def find_vector_candidates(
        self,
        tenant_id: str,
        query_embedding: list[float],
        top_k: int,
        status: str,
        filters: dict[str, object] | None = None,
    ) -> list[RetrievalCandidate]: ...

    async def find_exact_rule_matches(
        self,
        tenant_id: str,
        probe_fields: dict[str, str],
        normalized_probe_fields: dict[str, str],
        rules: list[DuplicateMatchRule],
        max_results: int,
        filters: dict[str, object] | None = None,
    ) -> list[RetrievalCandidate]: ...

    async def find_fuzzy_rule_matches(
        self,
        tenant_id: str,
        probe_fields: dict[str, str],
        rules: list[DuplicateMatchRule],
        expanded_terms: list[str],
        max_results: int,
        filters: dict[str, object] | None = None,
    ) -> list[RetrievalCandidate]: ...

    async def upsert_file_sync_state(self, tenant_id: str, state: dict[str, object]) -> None: ...
