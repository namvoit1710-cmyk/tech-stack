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

from smart_service_sdk.layer1_domain.entities.retrieval_candidate import (
    RetrievalCandidate,
)
from smart_service_sdk.layer1_domain.entities.retrieval_query import RetrievalRoute
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    SimilarityRuntimeConfig,
)
from smart_service_sdk.layer2_application.features.similarity.use_cases.similarity_usecase import (
    SimilarityCommand,
    SimilarityUseCase,
)


class _FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.1, 0.2, 0.3]


class _FakeRetrievalChunkRepository:
    def __init__(self, candidates: list[RetrievalCandidate]) -> None:
        self._candidates = candidates
        self.calls: list[dict[str, object]] = []

    async def find_vector_candidates(
        self,
        tenant_id: str,
        query_embedding: list[float],
        top_k: int,
        status: str,
        filters: dict[str, object] | None = None,
    ) -> list[RetrievalCandidate]:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "query_embedding": query_embedding,
                "top_k": top_k,
                "status": status,
                "filters": filters or {},
            }
        )
        return list(self._candidates)


class _FakeRetrievalGraphRepository:
    def __init__(self, candidates: list[RetrievalCandidate]) -> None:
        self._candidates = candidates
        self.calls: list[dict[str, object]] = []

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
    ) -> list[RetrievalCandidate]:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "query_embedding": query_embedding,
                "seed_texts": list(seed_texts),
                "filters": filters or {},
                "seed_top_k": seed_top_k,
                "neighbor_cap_per_seed": neighbor_cap_per_seed,
                "max_relation_candidates": max_relation_candidates,
                "max_graph_chunk_candidates": max_graph_chunk_candidates,
                "max_hops": max_hops,
            }
        )
        return list(self._candidates)


class _FakeRuntimeConfigurationService:
    def __init__(self, config: SimilarityRuntimeConfig) -> None:
        self._config = config

    async def get_similarity_config(self) -> SimilarityRuntimeConfig:
        return self._config


class _FakeLogger:
    def info(self, message: str, **kwargs) -> None:
        return None


class _FakeQueryEntityExtractor:
    def __init__(self, seed_texts: list[str]) -> None:
        self._seed_texts = seed_texts
        self.calls: list[str] = []

    def extract(self, text: str) -> list[str]:
        self.calls.append(text)
        return list(self._seed_texts)


def _candidate(
    *,
    document_id: str,
    chunk_id: str,
    score: float,
    content: str = "content",
    route: RetrievalRoute = RetrievalRoute.HYBRID_DOCUMENT,
    metadata: dict[str, object] | None = None,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        document_id=document_id,
        chunk_id=chunk_id,
        content=content,
        score=score,
        route=route,
        metadata=metadata or {},
    )


def _use_case(
    *,
    vector_candidates: list[RetrievalCandidate],
    graph_candidates: list[RetrievalCandidate],
    seed_texts: list[str],
    config: SimilarityRuntimeConfig | None = None,
):
    resolved_config = config or SimilarityRuntimeConfig(
        vector_top_k=5,
        vector_min_score=0.45,
        graph_seed_top_k=8,
        graph_neighbor_cap_per_seed=6,
        graph_max_relation_candidates=24,
        graph_max_graph_chunk_candidates=16,
        max_results=10,
    )
    embedding_provider = _FakeEmbeddingProvider()
    chunk_repository = _FakeRetrievalChunkRepository(vector_candidates)
    graph_repository = _FakeRetrievalGraphRepository(graph_candidates)
    extractor = _FakeQueryEntityExtractor(seed_texts)
    use_case = SimilarityUseCase(
        logger=_FakeLogger(),
        embedding_provider=embedding_provider,
        retrieval_chunk_repository=chunk_repository,
        retrieval_graph_repository=graph_repository,
        runtime_configuration_service=_FakeRuntimeConfigurationService(resolved_config),
        query_entity_extractor=extractor,
        default_tenant_id="TENANT-1",
    )
    return use_case, embedding_provider, chunk_repository, graph_repository, extractor


def test_similarity_vector_mode_only_calls_vector_and_applies_min_score() -> None:
    use_case, embedding_provider, chunk_repository, graph_repository, extractor = _use_case(
        vector_candidates=[
            _candidate(document_id="DOC-1", chunk_id="CHUNK-1", score=0.8),
            _candidate(document_id="DOC-2", chunk_id="CHUNK-2", score=0.2),
        ],
        graph_candidates=[],
        seed_texts=["ignored"],
    )

    result = asyncio.run(
        use_case.execute(
            SimilarityCommand(
                query_text="acme thailand",
                mode="VECTOR",
                filters={"allowed_document_ids": ["DOC-1", "DOC-2"]},
            )
        )
    )

    assert embedding_provider.calls == ["acme thailand"]
    assert len(chunk_repository.calls) == 1
    assert chunk_repository.calls[0]["top_k"] == 10
    assert not graph_repository.calls
    assert not extractor.calls
    assert [candidate.chunk_id for candidate in result.candidates] == ["CHUNK-1"]


def test_similarity_graph_mode_returns_empty_when_no_seed_texts() -> None:
    use_case, embedding_provider, chunk_repository, graph_repository, extractor = _use_case(
        vector_candidates=[_candidate(document_id="DOC-1", chunk_id="CHUNK-1", score=0.8)],
        graph_candidates=[_candidate(document_id="DOC-2", chunk_id="CHUNK-2", score=0.9)],
        seed_texts=[],
    )

    result = asyncio.run(
        use_case.execute(
            SimilarityCommand(
                query_text="  supplier without entities  ",
                mode="GRAPH",
            )
        )
    )

    assert embedding_provider.calls == ["  supplier without entities  "]
    assert not chunk_repository.calls
    assert extractor.calls == ["supplier without entities"]
    assert not graph_repository.calls
    assert result.candidates == []


def test_similarity_all_mode_merges_union_and_keeps_mode_metadata() -> None:
    use_case, _, chunk_repository, graph_repository, extractor = _use_case(
        vector_candidates=[
            _candidate(
                document_id="DOC-1",
                chunk_id="SHARED",
                score=0.61,
                metadata={"source": "vector"},
            ),
            _candidate(document_id="DOC-2", chunk_id="VECTOR-ONLY", score=0.72),
        ],
        graph_candidates=[
            _candidate(
                document_id="DOC-1",
                chunk_id="SHARED",
                score=0.93,
                metadata={"source": "graph"},
            ),
            _candidate(document_id="DOC-3", chunk_id="GRAPH-ONLY", score=0.88),
        ],
        seed_texts=["acme", "vendor"],
    )

    result = asyncio.run(
        use_case.execute(
            SimilarityCommand(
                query_text="acme vendor",
                mode="ALL",
                top_k=2,
            )
        )
    )

    assert len(chunk_repository.calls) == 1
    assert len(graph_repository.calls) == 1
    assert extractor.calls == ["acme vendor"]
    assert [candidate.chunk_id for candidate in result.candidates] == [
        "SHARED",
        "GRAPH-ONLY",
    ]

    shared_candidate = result.candidates[0]
    assert shared_candidate.score == 0.93
    assert shared_candidate.route is RetrievalRoute.HYBRID_DOCUMENT
    assert shared_candidate.metadata["vector_score"] == 0.61
    assert shared_candidate.metadata["graph_score"] == 0.93
    assert shared_candidate.metadata["contributing_modes"] == ["GRAPH", "VECTOR"]


def test_similarity_all_mode_keeps_vector_only_and_graph_only_candidates() -> None:
    use_case, _, _, _, _ = _use_case(
        vector_candidates=[
            _candidate(document_id="DOC-1", chunk_id="VECTOR-ONLY", score=0.77),
        ],
        graph_candidates=[
            _candidate(document_id="DOC-2", chunk_id="GRAPH-ONLY", score=0.65),
        ],
        seed_texts=["seed"],
    )

    result = asyncio.run(
        use_case.execute(
            SimilarityCommand(
                query_text="seed",
                mode="ALL",
                top_k=5,
            )
        )
    )

    candidates_by_chunk_id = {
        candidate.chunk_id: candidate for candidate in result.candidates
    }
    assert set(candidates_by_chunk_id) == {"VECTOR-ONLY", "GRAPH-ONLY"}
    assert candidates_by_chunk_id["VECTOR-ONLY"].metadata["contributing_modes"] == [
        "VECTOR"
    ]
    assert candidates_by_chunk_id["GRAPH-ONLY"].metadata["contributing_modes"] == [
        "GRAPH"
    ]
