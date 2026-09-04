import asyncio
from pathlib import Path

from smart_service_sdk.layer2_application.features.background_job.background_job_runner import (
    BackgroundJobRunner,
)
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    FieldSelectionConfig,
)
from smart_service_sdk.layer2_application.interfaces.graph_extractor_interface import (
    GraphExtractionResult,
)


class _Logger:
    def info(self, *args, **kwargs) -> None:
        del args, kwargs


class _FileServiceClient:
    def __init__(self, csv_path: Path, rows=None) -> None:
        self._csv_path = csv_path
        self._rows = rows or [{"Nickname": "Acme", "Custom Tax": "12345", "Notes": "Preferred"}]

    async def download_file(self, file_id, tenant_id, *, fallback_file_name):
        del file_id, tenant_id, fallback_file_name
        return str(self._csv_path), self._csv_path.name, "text/csv"

    def parse_csv_rows(self, file_path):
        del file_path
        return [dict(row) for row in self._rows]

    def content_hash(self, file_path):
        return Path(file_path).name


class _EmbeddingProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed_texts(self, texts):
        self.calls.append(list(texts))
        return [[float(i + 1)] for i, _ in enumerate(texts)]


class _ChunkRepository:
    async def replace_file_chunks(self, tenant_id, file_id, documents, chunks) -> None:
        del tenant_id, file_id, documents, chunks

    async def upsert_file_sync_state(self, tenant_id, state) -> None:
        del tenant_id, state


class _GraphRepository:
    async def replace_file_graphs(self, *, tenant_id, file_id, document_graphs) -> None:
        del tenant_id, file_id, document_graphs


class _RecordingGraphExtractor:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def extract(self, *, document_id, chunks) -> GraphExtractionResult:
        del document_id
        self.texts.append(chunks[0].text)
        return GraphExtractionResult()


class _ConfigService:
    def __init__(self, config: FieldSelectionConfig) -> None:
        self._config = config

    async def get_field_config(self) -> FieldSelectionConfig:
        return self._config


def _run(config: FieldSelectionConfig, rows=None):
    embedding_provider = _EmbeddingProvider()
    graph_extractor = _RecordingGraphExtractor()
    runner = BackgroundJobRunner(
        logger=_Logger(),
        file_service_client=_FileServiceClient(Path("ignored.csv"), rows=rows),
        embedding_provider=embedding_provider,
        retrieval_chunk_repository=_ChunkRepository(),
        retrieval_graph_repository=_GraphRepository(),
        graph_extractor=graph_extractor,
        runtime_configuration_service=_ConfigService(config),
        default_tenant_id="tenant-1",
    )
    asyncio.run(runner.run_import_job_for_file(file_id="file-1", tenant_id="tenant-1"))
    return embedding_provider, graph_extractor


def test_embedding_text_uses_only_configured_embedding_fields() -> None:
    embedding_provider, _ = _run(
        FieldSelectionConfig(embedding_fields=("Nickname",), graph_entity_fields=())
    )

    assert embedding_provider.calls == [["Nickname: Acme"]]


def test_graph_extraction_uses_only_configured_graph_fields() -> None:
    _, graph_extractor = _run(
        FieldSelectionConfig(embedding_fields=("Nickname",), graph_entity_fields=("Notes",))
    )

    # graph text is scoped to the graph-entity field, independent of the
    # (different) embedding-field selection
    assert graph_extractor.texts == ["Notes: Preferred"]


def test_empty_config_falls_back_to_all_fields_for_both_artifacts() -> None:
    embedding_provider, graph_extractor = _run(FieldSelectionConfig())

    assert embedding_provider.calls == [
        ["Custom Tax: 12345 | Nickname: Acme | Notes: Preferred"]
    ]
    assert graph_extractor.texts == [
        "Custom Tax: 12345 | Nickname: Acme | Notes: Preferred"
    ]


def test_matches_messy_header_whitespace_and_case() -> None:
    # CSV header carries a trailing space and different case than the config
    embedding_provider, _ = _run(
        FieldSelectionConfig(embedding_fields=("nickname",)),
        rows=[{"Nickname ": "Acme", "Notes": "Preferred"}],
    )
    # matched despite space+case; the actual header (with its trailing space) is used
    assert embedding_provider.calls == [["Nickname : Acme"]]


def test_embedding_never_empty_when_selection_absent_from_row() -> None:
    # configured field is absent from the row -> fall back to the whole row,
    # never an empty embedding vector
    embedding_provider, _ = _run(
        FieldSelectionConfig(embedding_fields=("Manufacturer",)),
        rows=[{"Nickname": "Acme", "Notes": "Preferred"}],
    )
    assert embedding_provider.calls == [["Nickname: Acme | Notes: Preferred"]]


def test_graph_stays_strict_when_selection_absent_from_row() -> None:
    # graph must NOT fall back: a row lacking the configured graph field
    # contributes no graph text (no entities)
    _, graph_extractor = _run(
        FieldSelectionConfig(graph_entity_fields=("Manufacturer",)),
        rows=[{"Nickname": "Acme", "Notes": "Preferred"}],
    )
    assert graph_extractor.texts == [""]
