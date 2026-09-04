import asyncio
from datetime import datetime
from io import BytesIO

import pytest

from app.layer1_domain.entities.import_data import GovernanceUploadResult
from app.layer2_application.features.import_data.use_cases.upload_import_file_usecase import (
    UploadImportFileCommand,
    UploadImportFileUseCase,
)
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    FieldSelectionConfig,
)


class _StubRuntimeConfigurationService:
    def __init__(self, config: FieldSelectionConfig | None = None) -> None:
        self._config = config or FieldSelectionConfig()

    async def get_field_config(self) -> FieldSelectionConfig:
        return self._config


class _StubUploadResultRepository:
    def __init__(self) -> None:
        self.saved_results: list[GovernanceUploadResult] = []

    async def save(
        self,
        result: GovernanceUploadResult,
    ) -> GovernanceUploadResult:
        self.saved_results.append(result)
        result.created_at = datetime(2026, 6, 22, 12, 0, 0)
        return result


class _StubEmbeddingProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(index + 1)] for index, _text in enumerate(texts)]


class _StubRetrievalChunkRepository:
    def __init__(self) -> None:
        self.replaced_chunks: list[
            tuple[str, str, list[dict[str, object]], list[dict[str, object]]]
        ] = []
        self.file_sync_states: list[tuple[str, dict[str, object]]] = []

    async def replace_file_chunks(
        self,
        tenant_id: str,
        file_id: str,
        documents: list[dict[str, object]],
        chunks: list[dict[str, object]],
    ) -> None:
        self.replaced_chunks.append((tenant_id, file_id, documents, chunks))

    async def upsert_file_sync_state(
        self,
        tenant_id: str,
        state: dict[str, object],
    ) -> None:
        self.file_sync_states.append((tenant_id, state))


def test_upload_import_file_usecase_parses_and_saves_metadata() -> None:
    repository = _StubUploadResultRepository()
    embedding_provider = _StubEmbeddingProvider()
    retrieval_chunk_repository = _StubRetrievalChunkRepository()
    use_case = UploadImportFileUseCase(
        repository,
        embedding_provider,
        retrieval_chunk_repository,
        _StubRuntimeConfigurationService(),
        "default-tenant",
    )

    result = asyncio.run(
        use_case.execute(
            UploadImportFileCommand(
                file_name=" sample.csv ",
                content=BytesIO(b"name,city\nacme,Bangkok\n"),
                tenant_id="tenant-1",
            )
        )
    )

    assert result.result_id.startswith("gov-upload-")
    assert result.file_name == "sample.csv"
    assert result.tenant_id == "tenant-1"
    assert result.row_count == 1
    assert result.raw_headers == ["name", "city"]
    assert result.parsed_rows == [{"name": "acme", "city": "Bangkok"}]
    assert repository.saved_results[0].file_name == "sample.csv"
    assert embedding_provider.calls == [["name: acme\ncity: Bangkok"]]
    tenant_id, file_id, documents, chunks = retrieval_chunk_repository.replaced_chunks[0]
    assert tenant_id == "tenant-1"
    assert file_id == result.result_id
    assert documents[0]["doc_type"] == "governance_upload"
    assert chunks[0]["row_number"] == 1
    assert chunks[0]["embedding"] == [1.0]
    assert chunks[0]["content_json"] == {
        "fields": {"name": "acme", "city": "Bangkok"},
        "normalized_fields": {"name": "acme", "city": "bangkok"},
    }
    assert retrieval_chunk_repository.file_sync_states[0][0] == "tenant-1"


def test_upload_import_file_usecase_rejects_empty_file_name() -> None:
    use_case = UploadImportFileUseCase(
        _StubUploadResultRepository(),
        _StubEmbeddingProvider(),
        _StubRetrievalChunkRepository(),
        _StubRuntimeConfigurationService(),
        "default-tenant",
    )

    with pytest.raises(ValueError, match="file_name must not be empty"):
        asyncio.run(
            use_case.execute(
                UploadImportFileCommand(
                    file_name="   ",
                    content=BytesIO(b""),
                    tenant_id=None,
                )
            )
        )


def test_upload_import_file_usecase_rejects_csv_without_data_rows() -> None:
    use_case = UploadImportFileUseCase(
        _StubUploadResultRepository(),
        _StubEmbeddingProvider(),
        _StubRetrievalChunkRepository(),
        _StubRuntimeConfigurationService(),
        "default-tenant",
    )

    with pytest.raises(ValueError, match="uploaded CSV must contain at least one data row"):
        asyncio.run(
            use_case.execute(
                UploadImportFileCommand(
                    file_name="sample.csv",
                    content=BytesIO(b"name,city\n,\n"),
                    tenant_id=None,
                )
            )
        )


def test_upload_import_file_usecase_uses_default_tenant_for_saved_records() -> None:
    repository = _StubUploadResultRepository()
    embedding_provider = _StubEmbeddingProvider()
    retrieval_chunk_repository = _StubRetrievalChunkRepository()
    use_case = UploadImportFileUseCase(
        repository,
        embedding_provider,
        retrieval_chunk_repository,
        _StubRuntimeConfigurationService(),
        "default-tenant",
    )

    result = asyncio.run(
        use_case.execute(
            UploadImportFileCommand(
                file_name="sample.csv",
                content=BytesIO(b"name,city\nacme,Bangkok\n"),
                tenant_id=None,
            )
        )
    )

    assert result.tenant_id == "default-tenant"
    assert retrieval_chunk_repository.replaced_chunks[0][0] == "default-tenant"


def test_upload_embeds_only_configured_fields() -> None:
    # SA-1451: with an embedding-field selection, only those columns feed the
    # embedded text; other columns still parse but are excluded from the vector.
    repository = _StubUploadResultRepository()
    embedding_provider = _StubEmbeddingProvider()
    retrieval_chunk_repository = _StubRetrievalChunkRepository()
    use_case = UploadImportFileUseCase(
        repository,
        embedding_provider,
        retrieval_chunk_repository,
        _StubRuntimeConfigurationService(
            FieldSelectionConfig(embedding_fields=("name",))
        ),
        "default-tenant",
    )

    asyncio.run(
        use_case.execute(
            UploadImportFileCommand(
                file_name="sample.csv",
                content=BytesIO(b"name,city\nacme,Bangkok\n"),
                tenant_id="tenant-1",
            )
        )
    )

    # Only 'name' is embedded; 'city' is excluded from the embedded text.
    assert embedding_provider.calls == [["name: acme"]]
    _, _, _, chunks = retrieval_chunk_repository.replaced_chunks[0]
    assert chunks[0]["metadata"]["embedding_fields"] == ["name"]


class _StubSaveResultOnlyRepository:
    """Repository exposing only ``save_result`` (no ``save``) — exercises the
    _save_upload_result fallback branch."""

    def __init__(self) -> None:
        self.saved_results: list[GovernanceUploadResult] = []

    async def save_result(
        self,
        result: GovernanceUploadResult,
    ) -> GovernanceUploadResult:
        self.saved_results.append(result)
        result.created_at = datetime(2026, 6, 22, 12, 0, 0)
        return result


class _StubNoPersistRepository:
    """Repository implementing neither ``save`` nor ``save_result``."""


def test_upload_import_file_usecase_uses_save_result_fallback() -> None:
    repository = _StubSaveResultOnlyRepository()
    use_case = UploadImportFileUseCase(
        repository,
        _StubEmbeddingProvider(),
        _StubRetrievalChunkRepository(),
        _StubRuntimeConfigurationService(),
        "default-tenant",
    )

    result = asyncio.run(
        use_case.execute(
            UploadImportFileCommand(
                file_name="sample.csv",
                content=BytesIO(b"name,city\nacme,Bangkok\n"),
                tenant_id="tenant-1",
            )
        )
    )

    assert result.result_id.startswith("gov-upload-")
    assert repository.saved_results[0].file_name == "sample.csv"
    assert result.created_at == datetime(2026, 6, 22, 12, 0, 0)


def test_upload_import_file_usecase_requires_save_or_save_result() -> None:
    use_case = UploadImportFileUseCase(
        _StubNoPersistRepository(),
        _StubEmbeddingProvider(),
        _StubRetrievalChunkRepository(),
        _StubRuntimeConfigurationService(),
        "default-tenant",
    )

    with pytest.raises(
        AttributeError,
        match="upload_result_repository must implement save or save_result",
    ):
        asyncio.run(
            use_case.execute(
                UploadImportFileCommand(
                    file_name="sample.csv",
                    content=BytesIO(b"name,city\nacme,Bangkok\n"),
                    tenant_id="tenant-1",
                )
            )
        )


# --- edge cases / invalid inputs / boundary conditions ---


def _run_upload(content: bytes, *, file_name: str = "sample.csv", tenant_id="tenant-1"):
    repository = _StubUploadResultRepository()
    embedding_provider = _StubEmbeddingProvider()
    retrieval_chunk_repository = _StubRetrievalChunkRepository()
    use_case = UploadImportFileUseCase(
        repository,
        embedding_provider,
        retrieval_chunk_repository,
        _StubRuntimeConfigurationService(),
        "default-tenant",
    )
    result = asyncio.run(
        use_case.execute(
            UploadImportFileCommand(
                file_name=file_name,
                content=BytesIO(content),
                tenant_id=tenant_id,
            )
        )
    )
    return result, repository, embedding_provider, retrieval_chunk_repository


def test_upload_skips_rows_whose_only_column_has_a_whitespace_header() -> None:
    # BOUNDARY / edge: a column whose header is whitespace survives CSV parsing
    # (the header key is truthy) but contributes no field once trimmed, so the
    # row is skipped by `_build_chunk_rows` (the defensive `if not fields`).
    result, _repo, embedding_provider, chunk_repo = _run_upload(b" \nfoo\n")

    # The row still counts, but produces no headers and no embeddable chunks.
    assert result.row_count == 1
    assert result.raw_headers == []
    assert embedding_provider.calls == [[]]
    _tenant, _file_id, documents, chunks = chunk_repo.replaced_chunks[0]
    assert documents == []
    assert chunks == []


def test_upload_rejects_non_utf8_bytes() -> None:
    # INVALID INPUT: non-UTF-8 content is not guarded and surfaces as a raw
    # UnicodeDecodeError (documents current behaviour — see finding F-EAGLE-2).
    with pytest.raises(UnicodeDecodeError):
        _run_upload(b"\xff\xfe\x00not-utf8")


def test_upload_handles_multiple_data_rows_in_order() -> None:
    # BOUNDARY: more than one data row -> row_count and per-row chunk numbering.
    result, _repo, embedding_provider, chunk_repo = _run_upload(
        b"name\nalpha\nbeta\ngamma\n"
    )

    assert result.row_count == 3
    _tenant, _file_id, _documents, chunks = chunk_repo.replaced_chunks[0]
    assert [chunk["row_number"] for chunk in chunks] == [1, 2, 3]
    assert embedding_provider.calls == [["name: alpha", "name: beta", "name: gamma"]]


def test_upload_trims_whitespace_in_field_values() -> None:
    # EDGE: values are trimmed when composing chunk fields, even though the raw
    # parsed_rows preserve the original (untrimmed) cell text.
    result, _repo, _embedding_provider, chunk_repo = _run_upload(
        b"name,city\n  acme  ,  Bangkok  \n"
    )

    assert result.parsed_rows == [{"name": "  acme  ", "city": "  Bangkok  "}]
    _tenant, _file_id, _documents, chunks = chunk_repo.replaced_chunks[0]
    assert chunks[0]["metadata"]["fields"] == {"name": "acme", "city": "Bangkok"}
