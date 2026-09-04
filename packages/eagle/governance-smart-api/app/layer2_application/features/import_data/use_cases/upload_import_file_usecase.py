import csv
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from typing import BinaryIO
from uuid import uuid4

from app.layer1_domain.entities.import_data import (
    GovernanceUploadAcceptance,
    GovernanceUploadResult,
)
from app.layer2_application.interfaces.upload_result_repository_interface import (
    IUploadResultRepository,
)
from smart_service_sdk.layer2_application.features.field_configuration.field_selection import (
    compose_field_text,
    resolve_included_fields,
)
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    RuntimeConfigurationService,
)
from smart_service_sdk.layer2_application.interfaces.embedding_provider_interface import (
    IEmbeddingProvider,
)
from smart_service_sdk.layer2_application.repositories.retrieval_chunk_repository_interface import (
    IRetrievalChunkRepository,
)


@dataclass(frozen=True)
class UploadImportFileCommand:
    file_name: str
    content: BinaryIO
    tenant_id: str | None = None


class UploadImportFileUseCase:
    def __init__(
        self,
        upload_result_repository: IUploadResultRepository,
        embedding_provider: IEmbeddingProvider,
        retrieval_chunk_repository: IRetrievalChunkRepository,
        runtime_configuration_service: RuntimeConfigurationService,
        default_tenant_id: str,
    ):
        self._upload_result_repository = upload_result_repository
        self._embedding_provider = embedding_provider
        self._retrieval_chunk_repository = retrieval_chunk_repository
        self._runtime_configuration_service = runtime_configuration_service
        self._default_tenant_id = default_tenant_id

    async def execute(
        self,
        command: UploadImportFileCommand,
    ) -> GovernanceUploadAcceptance:
        normalized_file_name = command.file_name.strip()
        if not normalized_file_name:
            raise ValueError("file_name must not be empty")

        raw_bytes = command.content.read()
        decoded_text = raw_bytes.decode("utf-8-sig")
        parsed_rows = self._parse_csv_rows(decoded_text)
        if not parsed_rows:
            raise ValueError("uploaded CSV must contain at least one data row")

        resolved_tenant_id = (command.tenant_id or self._default_tenant_id).strip()
        result_id = f"gov-upload-{uuid4()}"
        raw_headers = [header for header in parsed_rows[0].keys() if str(header).strip()]
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        field_config = await self._runtime_configuration_service.get_field_config()
        documents, chunks = self._build_chunk_rows(
            tenant_id=resolved_tenant_id,
            source_file_id=result_id,
            file_name=normalized_file_name,
            parsed_rows=parsed_rows,
            embedding_fields=field_config.embedding_fields,
        )
        embeddings = await self._embedding_provider.embed_texts(
            [str(row["content"]) for row in chunks]
        )
        for row, embedding in zip(chunks, embeddings, strict=False):
            row["embedding"] = embedding

        await self._retrieval_chunk_repository.replace_file_chunks(
            resolved_tenant_id,
            result_id,
            documents,
            chunks,
        )
        await self._retrieval_chunk_repository.upsert_file_sync_state(
            resolved_tenant_id,
            {
                "file_id": result_id,
                "source_updated_at": None,
                "last_indexed_at": datetime.now(UTC),
                "parse_status": "indexed",
                "content_hash": content_hash,
                "metadata": {
                    "file_name": normalized_file_name,
                    "row_count": len(chunks),
                    "upload_kind": "governance_csv",
                },
            },
        )
        upload_result = GovernanceUploadResult(
            id=result_id,
            file_name=normalized_file_name,
            content_hash=content_hash,
            tenant_id=resolved_tenant_id,
            row_count=len(parsed_rows),
            raw_headers=raw_headers,
            parsed_rows=parsed_rows,
        )
        saved_result = await self._save_upload_result(upload_result)
        return GovernanceUploadAcceptance(
            result_id=str(saved_result.id),
            file_name=normalized_file_name,
            content_hash=content_hash,
            tenant_id=resolved_tenant_id,
            row_count=saved_result.row_count,
            raw_headers=list(saved_result.raw_headers),
            parsed_rows=list(saved_result.parsed_rows),
            created_at=saved_result.created_at,
        )

    async def _save_upload_result(
        self,
        upload_result: GovernanceUploadResult,
    ) -> GovernanceUploadResult:
        save = getattr(self._upload_result_repository, "save", None)
        if callable(save):
            return await save(upload_result)
        save_result = getattr(self._upload_result_repository, "save_result", None)
        if callable(save_result):
            return await save_result(upload_result)
        raise AttributeError("upload_result_repository must implement save or save_result")

    @staticmethod
    def _normalize_text(value: object) -> str:
        return " ".join(str(value).strip().lower().split())

    @classmethod
    def _parse_csv_rows(cls, content: str) -> list[dict[str, str]]:
        reader = csv.DictReader(StringIO(content, newline=""))
        rows = [
            {str(key): str(value or "") for key, value in row.items() if key}
            for row in reader
        ]
        return [
            row
            for row in rows
            if any(str(value).strip() for value in row.values())
        ]

    @classmethod
    def _build_chunk_rows(
        cls,
        *,
        tenant_id: str,
        source_file_id: str,
        file_name: str,
        parsed_rows: list[dict[str, str]],
        embedding_fields: tuple[str, ...] = (),
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        documents: list[dict[str, object]] = []
        chunks: list[dict[str, object]] = []
        for row_number, row in enumerate(parsed_rows, start=1):
            fields = {
                str(source_header): str(value).strip()
                for source_header, value in row.items()
                if str(source_header).strip() and str(value).strip()
            }
            if not fields:
                continue
            # SA-1451: embedded text is composed from the configured embedding
            # fields only (empty selection -> every field, in header order).
            available_fields = [
                str(field_name)
                for field_name in row.keys()
                if str(field_name).strip() and fields.get(str(field_name), "").strip()
            ]
            embedded_field_names = resolve_included_fields(
                available_fields, embedding_fields, fallback_to_all_when_empty=True
            )
            content = compose_field_text(
                fields, embedded_field_names, separator="\n"
            )
            document_id = f"{source_file_id}:{row_number}"
            chunk_id = f"{document_id}:row"
            row_content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            metadata = {
                "file_id": source_file_id,
                "row_number": row_number,
                "fields": fields,
                "raw_headers": list(row.keys()),
                "upload_kind": "governance_csv",
                "embedding_fields": list(embedded_field_names),
            }
            content_json = {
                "fields": {
                    cls._normalize_text(field_name): field_value
                    for field_name, field_value in fields.items()
                },
                "normalized_fields": {
                    cls._normalize_text(field_name): cls._normalize_text(field_value)
                    for field_name, field_value in fields.items()
                },
            }
            documents.append(
                {
                    "document_id": document_id,
                    "source_file_id": source_file_id,
                    "content_hash": row_content_hash,
                    "status": "indexed",
                    "doc_type": "governance_upload",
                    "mime_type": "text/csv",
                    "filename": file_name,
                    "metadata": metadata,
                }
            )
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "source_file_id": source_file_id,
                    "content_hash": row_content_hash,
                    "status": "indexed",
                    "doc_type": "governance_upload",
                    "mime_type": "text/csv",
                    "row_number": row_number,
                    "chunk_kind": "structured_row",
                    "content_format": "text",
                    "content": content,
                    "content_json": content_json,
                    "metadata": metadata,
                }
            )
        return documents, chunks
