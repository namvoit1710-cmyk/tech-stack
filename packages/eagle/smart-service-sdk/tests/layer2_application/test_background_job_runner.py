import asyncio
from pathlib import Path

from smart_service_sdk.layer1_domain.entities.background_job_record import (
    BackgroundJobRecord,
)
from smart_service_sdk.layer2_application.features.background_job.background_job_coordinator import (
    BackgroundJobCoordinator,
)
from smart_service_sdk.layer2_application.features.background_job.background_job_runner import (
    BackgroundJobRunner,
)
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    FieldSelectionConfig,
)
from smart_service_sdk.layer2_application.interfaces.graph_extractor_interface import (
    ExtractedGraphEntity,
    ExtractedGraphMention,
    GraphExtractionResult,
)


class _ConfigService:
    """Fake RuntimeConfigurationService exposing only get_field_config."""

    def __init__(self, config: FieldSelectionConfig | None = None) -> None:
        self._config = config or FieldSelectionConfig()

    async def get_field_config(self) -> FieldSelectionConfig:
        return self._config


class _Logger:
    def info(self, *args, **kwargs) -> None:
        del args, kwargs

    def exception(self, *args, **kwargs) -> None:
        del args, kwargs


class _FileServiceClient:
    def __init__(self, csv_path: Path) -> None:
        self._csv_path = csv_path

    async def download_file(self, file_id: str, tenant_id: str, *, fallback_file_name: str):
        del file_id, tenant_id, fallback_file_name
        return str(self._csv_path), self._csv_path.name, "text/csv"

    def parse_csv_rows(self, file_path: str) -> list[dict[str, str]]:
        del file_path
        return [
            {"Nickname": "Acme", "Custom Tax": "12345", "Notes": "Preferred"},
            {"Nickname": "", "Custom Tax": "", "Notes": ""},
            {"Nickname": "Beta", "Custom Tax": "", "Notes": "Needs review"},
        ]

    def content_hash(self, file_path: str) -> str:
        return Path(file_path).name


class _EmbeddingProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(index + 1)] for index, _ in enumerate(texts)]


class _ChunkRepository:
    def __init__(self) -> None:
        self.replaced_documents: list[dict[str, object]] = []
        self.replaced_chunks: list[dict[str, object]] = []
        self.file_state: dict[str, object] | None = None

    async def replace_file_chunks(
        self,
        tenant_id: str,
        file_id: str,
        documents: list[dict[str, object]],
        chunks: list[dict[str, object]],
    ) -> None:
        del tenant_id, file_id
        self.replaced_documents = [dict(document) for document in documents]
        self.replaced_chunks = [dict(chunk) for chunk in chunks]

    async def upsert_file_sync_state(self, tenant_id: str, state: dict[str, object]) -> None:
        del tenant_id
        self.file_state = dict(state)


class _GraphRepository:
    def __init__(self) -> None:
        self.replaced_files: list[tuple[str, str, list[str]]] = []

    async def replace_file_graphs(
        self,
        *,
        tenant_id: str,
        file_id: str,
        document_graphs: list[tuple[str, GraphExtractionResult]],
    ) -> None:
        self.replaced_files.append(
            (
                tenant_id,
                file_id,
                [document_id for document_id, _ in document_graphs],
            )
        )


class _GraphExtractor:
    def extract(self, *, document_id: str, chunks) -> GraphExtractionResult:
        chunk = chunks[0]
        return GraphExtractionResult(
            entities=(
                ExtractedGraphEntity(
                    entity_id=f"{document_id}-entity",
                    canonical_name=str(chunk.text).split(" | ")[0],
                    entity_type="ROW",
                ),
            ),
            mentions=(
                ExtractedGraphMention(
                    entity_id=f"{document_id}-entity",
                    chunk_id=chunk.chunk_id,
                    surface_text=str(chunk.text),
                    start_offset=0,
                    end_offset=1,
                ),
            ),
        )


class _BackgroundJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[tuple[str, str], BackgroundJobRecord] = {}

    async def save(self, job: BackgroundJobRecord) -> BackgroundJobRecord:
        assert job.id is not None
        self.jobs[(job.tenant_id, job.id)] = job
        return job

    async def get(self, tenant_id: str, job_id: str) -> BackgroundJobRecord | None:
        return self.jobs.get((tenant_id, job_id))

    async def list_by_status(self, status: str) -> list[BackgroundJobRecord]:
        return [job for job in self.jobs.values() if job.status == status]


async def _wait_for_tasks(coordinator: BackgroundJobCoordinator) -> None:
    if coordinator._tasks:
        await asyncio.wait(coordinator._tasks.values())


def test_background_job_runner_indexes_csv_rows_as_chunks(tmp_path: Path) -> None:
    csv_path = tmp_path / "source.csv"
    csv_path.write_text("ignored", encoding="utf-8")

    chunk_repository = _ChunkRepository()
    graph_repository = _GraphRepository()
    embedding_provider = _EmbeddingProvider()
    runner = BackgroundJobRunner(
        logger=_Logger(),
        file_service_client=_FileServiceClient(csv_path),
        embedding_provider=embedding_provider,
        retrieval_chunk_repository=chunk_repository,
        retrieval_graph_repository=graph_repository,
        graph_extractor=_GraphExtractor(),
        runtime_configuration_service=_ConfigService(),
        default_tenant_id="tenant-1",
    )

    result = asyncio.run(
        runner.run_import_job_for_file(file_id="file-123", tenant_id="tenant-1")
    )

    assert result.row_count == 2
    assert len(chunk_repository.replaced_documents) == 2
    assert len(chunk_repository.replaced_chunks) == 2
    assert chunk_repository.replaced_chunks[0]["row_number"] == 1
    assert chunk_repository.replaced_chunks[1]["row_number"] == 3
    assert embedding_provider.calls == [
        [
            "Custom Tax: 12345 | Nickname: Acme | Notes: Preferred",
            "Nickname: Beta | Notes: Needs review",
        ]
    ]
    assert [row["embedding"] for row in chunk_repository.replaced_chunks] == [
        [1.0],
        [2.0],
    ]
    assert chunk_repository.replaced_chunks[0]["searchable_fields"] == [
        {
            "field_name": "nickname",
            "field_value": "Acme",
            "normalized_field_value": "acme",
            "row_number": 1,
        },
        {
            "field_name": "custom tax",
            "field_value": "12345",
            "normalized_field_value": "12345",
            "row_number": 1,
        },
        {
            "field_name": "notes",
            "field_value": "Preferred",
            "normalized_field_value": "preferred",
            "row_number": 1,
        },
    ]
    assert "content_json" not in chunk_repository.replaced_chunks[0]
    assert graph_repository.replaced_files == [
        (
            "tenant-1",
            "file-123",
            [
                chunk_repository.replaced_documents[0]["document_id"],
                chunk_repository.replaced_documents[1]["document_id"],
            ],
        )
    ]
    assert chunk_repository.file_state is not None
    assert chunk_repository.file_state["parse_status"] == "indexed"


def test_create_import_job_uses_background_runner(tmp_path: Path) -> None:
    csv_path = tmp_path / "source.csv"
    csv_path.write_text("ignored", encoding="utf-8")

    coordinator = BackgroundJobCoordinator(
        logger=_Logger(),
        background_job_repository=_BackgroundJobRepository(),
        job_runner=BackgroundJobRunner(
            logger=_Logger(),
            file_service_client=_FileServiceClient(csv_path),
            embedding_provider=_EmbeddingProvider(),
            retrieval_chunk_repository=_ChunkRepository(),
            retrieval_graph_repository=_GraphRepository(),
            graph_extractor=_GraphExtractor(),
            runtime_configuration_service=_ConfigService(),
            default_tenant_id="tenant-1",
        ),
        default_tenant_id="tenant-1",
    )

    async def scenario() -> None:
        accepted = await coordinator.create_import_job(
            tenant_id="tenant-1",
            file_ids=["file-123"],
        )
        assert accepted.status == "accepted"
        await _wait_for_tasks(coordinator)
        persisted = await coordinator.get_background_job(
            tenant_id="tenant-1",
            job_id=accepted.job_id,
        )
        assert persisted is not None
        assert persisted.status == "completed"

    asyncio.run(scenario())
