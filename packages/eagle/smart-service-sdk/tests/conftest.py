import sys
from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:  # pragma: no cover
    TestClient = None


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _runtime_setting_defaults() -> dict[str, str]:
    from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
        RUNTIME_SETTING_DEFAULTS,
    )

    return dict(RUNTIME_SETTING_DEFAULTS)


def _retrieval_candidate(
    *,
    document_id: str,
    chunk_id: str,
    content: str,
    score: float,
    route_name: str,
    metadata: dict[str, object],
):
    from smart_service_sdk.layer1_domain.entities.retrieval_candidate import (
        RetrievalCandidate,
    )
    from smart_service_sdk.layer1_domain.entities.retrieval_query import RetrievalRoute

    return RetrievalCandidate(
        document_id=document_id,
        chunk_id=chunk_id,
        content=content,
        score=score,
        route=getattr(RetrievalRoute, route_name),
        metadata=metadata,
    )


class _FakeEmbeddingProvider:
    async def embed_query(self, text: str) -> list[float]:
        return [float(len(" ".join(text.split())))]

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(index + 1)] for index, _ in enumerate(texts)]


class _FakeLlmService:
    async def expand_terms(self, text: str) -> list[str]:
        normalized = " ".join(str(text).split()).strip()
        if not normalized:
            return []
        return [normalized]


class _FakeRuntimeSettingRepository:
    def __init__(self) -> None:
        self._values = _runtime_setting_defaults()

    async def get(self, key: str) -> str | None:
        return self._values.get(key)

    async def set(self, key: str, value: str) -> None:
        self._values[key] = value

    async def get_many(self, keys: list[str]) -> dict[str, str]:
        return {key: self._values[key] for key in keys if key in self._values}


class _FakeRetrievalChunkRepository:
    def __init__(self) -> None:
        self.file_state: dict[str, object] | None = None
        self._structured_candidate = _retrieval_candidate(
            document_id="doc-acme",
            chunk_id="chunk-1",
            content=(
                "## Sheet: Materials\n\n"
                "Table anchor: Material Catalog\n"
                "Rows 1-1 of 1\n\n"
                "| Material Name | Material Description |\n"
                "| --- | --- |\n"
                "| Finished Product: Wireless Earbuds | Wireless Earbuds |"
            ),
            score=0.0,
            route_name="STRUCTURED_TABLE",
            metadata={
                "chunk_role": "row_window",
                "source_file_id": "file-1",
                "search_rows": [
                    {
                        "row_number": 1,
                        "fields": {
                            "material name": "Finished Product: Wireless Earbuds",
                            "material description": "Wireless Earbuds",
                        },
                        "normalized_fields": {
                            "material name": "finished product: wireless earbuds",
                            "material description": "wireless earbuds",
                        },
                    }
                ],
            },
        )

    async def replace_file_chunks(
        self,
        tenant_id: str,
        file_id: str,
        documents: list[dict[str, object]],
        chunks: list[dict[str, object]],
    ) -> None:
        del tenant_id, file_id, documents, chunks

    async def find_vector_candidates(
        self,
        tenant_id: str,
        query_embedding: list[float],
        top_k: int,
        status: str,
        filters: dict[str, object] | None = None,
    ):
        del tenant_id, query_embedding, status, filters
        return [
            _retrieval_candidate(
                document_id="doc-acme",
                chunk_id="chunk-1",
                content="Acme Industrial Company Bangkok",
                score=0.91,
                route_name="STRUCTURED_TABLE",
                metadata={"source": "vector", "row_number": 1},
            )
        ][:top_k]

    async def find_exact_rule_matches(
        self,
        tenant_id: str,
        probe_fields: dict[str, str],
        normalized_probe_fields: dict[str, str],
        rules,
        max_results: int,
        filters: dict[str, object] | None = None,
    ):
        del tenant_id, probe_fields, filters, max_results
        matched_fields = []
        for rule in rules:
            if str(rule.match_type).strip().lower() != "exact":
                continue
            field_name = str(rule.field).strip().lower()
            if (
                self._structured_candidate.metadata["search_rows"][0]["normalized_fields"].get(field_name)
                == normalized_probe_fields.get(field_name)
            ):
                matched_fields.append(field_name)
        if not matched_fields:
            return []
        return [
            _retrieval_candidate(
                document_id=self._structured_candidate.document_id,
                chunk_id=self._structured_candidate.chunk_id,
                content=self._structured_candidate.content,
                score=0.0,
                route_name="STRUCTURED_TABLE",
                metadata={
                    **self._structured_candidate.metadata,
                    "exact_matches": matched_fields,
                },
            )
        ]

    async def find_fuzzy_rule_matches(
        self,
        tenant_id: str,
        probe_fields: dict[str, str],
        rules,
        expanded_terms: list[str],
        max_results: int,
        filters: dict[str, object] | None = None,
    ):
        del tenant_id, expanded_terms, filters, max_results
        fuzzy_matches: dict[str, float] = {}
        for rule in rules:
            if str(rule.match_type).strip().lower() != "fuzzy":
                continue
            field_name = str(rule.field).strip().lower()
            probe_value = str(probe_fields.get(field_name, "")).strip().lower()
            row_value = str(
                self._structured_candidate.metadata["search_rows"][0]["fields"].get(field_name, "")
            ).strip().lower()
            if probe_value and row_value and (probe_value in row_value or row_value in probe_value):
                fuzzy_matches[field_name] = 0.95
        if not fuzzy_matches:
            return []
        return [
            _retrieval_candidate(
                document_id=self._structured_candidate.document_id,
                chunk_id=self._structured_candidate.chunk_id,
                content=self._structured_candidate.content,
                score=0.0,
                route_name="STRUCTURED_TABLE",
                metadata={
                    **self._structured_candidate.metadata,
                    "fuzzy_matches": fuzzy_matches,
                    "matched_terms": list(probe_fields.values()),
                },
            )
        ]

    async def upsert_file_sync_state(self, tenant_id: str, state: dict[str, object]) -> None:
        del tenant_id
        self.file_state = dict(state)


class _FakeRetrievalGraphRepository:
    async def replace_file_graphs(
        self,
        *,
        tenant_id: str,
        file_id: str,
        document_graphs: list[tuple[str, object]],
    ) -> None:
        del tenant_id, file_id, document_graphs

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
    ):
        del tenant_id
        del query_embedding
        del filters
        del seed_top_k
        del neighbor_cap_per_seed
        del max_relation_candidates
        del max_graph_chunk_candidates
        del max_hops
        if not seed_texts:
            return []
        return [
            _retrieval_candidate(
                document_id="doc-acme",
                chunk_id="chunk-1",
                content="Acme Industrial Company Bangkok",
                score=0.88,
                route_name="HYBRID_DOCUMENT",
                metadata={"source": "graph"},
            )
        ]


class _FakeBackgroundJobRepository:
    def __init__(self) -> None:
        self._jobs = {}

    async def save(self, job):
        self._jobs[(job.tenant_id, str(job.id))] = job
        return job

    async def get(self, tenant_id: str, job_id: str):
        return self._jobs.get((tenant_id, job_id))

    async def list_by_status(self, status: str):
        return [job for job in self._jobs.values() if getattr(job, "status", "") == status]


class _FakeBackgroundJobRunner:
    def __init__(self, **kwargs) -> None:
        self._kwargs = kwargs
        self.calls: list[tuple[str, str]] = []

    async def run_import_job_for_file(self, file_id: str, tenant_id: str):
        self.calls.append((tenant_id, file_id))

        class _Result:
            row_count = 1
            metadata = {"file_id": file_id}

        return _Result()


class _FakeNlp:
    def __init__(self) -> None:
        self.pipe_names = ["tok2vec", "tagger", "parser", "ner"]
        self.meta = {"name": "en_core_web_sm"}


def _test_database_dependencies() -> dict:
    return {
        "database_backend": "hana",
        "embedding_provider": _FakeEmbeddingProvider(),
        "background_job_repository": _FakeBackgroundJobRepository(),
        "retrieval_chunk_repository": _FakeRetrievalChunkRepository(),
        "retrieval_graph_repository": _FakeRetrievalGraphRepository(),
        "runtime_setting_repository": _FakeRuntimeSettingRepository(),
    }


@pytest.fixture()
def built_container() -> dict:
    pytest.importorskip("pydantic_settings")
    import smart_service_sdk.bootstrap as bootstrap_module

    async def _fake_build_database_dependencies(logger, **kwargs):
        del logger, kwargs
        return _test_database_dependencies()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        bootstrap_module,
        "_build_database_dependencies",
        _fake_build_database_dependencies,
    )
    monkeypatch.setattr(bootstrap_module, "BackgroundJobRunner", _FakeBackgroundJobRunner)
    monkeypatch.setattr(bootstrap_module, "load_spacy_model", lambda _: _FakeNlp())
    monkeypatch.setattr(bootstrap_module, "StubLlmServiceClient", _FakeLlmService)
    try:
        return bootstrap_module.build_app_container()
    finally:
        monkeypatch.undo()


@pytest.fixture()
def api_client():
    if TestClient is None:  # pragma: no cover
        pytest.skip("fastapi is not installed")
    pytest.importorskip("pydantic_settings")
    import smart_service_sdk.bootstrap as bootstrap_module
    from smart_service_sdk import smart_create_app

    async def _fake_build_database_dependencies(logger, **kwargs):
        del logger, kwargs
        return _test_database_dependencies()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        bootstrap_module,
        "_build_database_dependencies",
        _fake_build_database_dependencies,
    )
    monkeypatch.setattr(bootstrap_module, "BackgroundJobRunner", _FakeBackgroundJobRunner)
    monkeypatch.setattr(bootstrap_module, "load_spacy_model", lambda _: _FakeNlp())
    monkeypatch.setattr(bootstrap_module, "StubLlmServiceClient", _FakeLlmService)
    app = smart_create_app()

    try:
        with TestClient(app) as client:
            yield client
    finally:
        monkeypatch.undo()
