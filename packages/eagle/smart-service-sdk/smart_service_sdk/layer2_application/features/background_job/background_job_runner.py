from __future__ import annotations

import asyncio
import hashlib
import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from smart_service_sdk.layer2_application.features.field_configuration.field_selection import (
    compose_field_text,
    resolve_included_fields,
)
from smart_service_sdk.layer2_application.interfaces.graph_extractor_interface import (
    GraphSourceChunk,
)


@dataclass(frozen=True)
class PreparedChunkRow:
    document_row: dict[str, object]
    chunk_row: dict[str, object]


@dataclass(frozen=True)
class DuplicateIndexImportResult:
    file_id: str
    row_count: int
    metadata: dict[str, object]


class BackgroundJobRunner:
    def __init__(
        self,
        *,
        logger,
        file_service_client,
        embedding_provider,
        retrieval_chunk_repository,
        retrieval_graph_repository,
        graph_extractor,
        runtime_configuration_service,
        default_tenant_id: str,
    ) -> None:
        self.logger = logger
        self.file_service_client = file_service_client
        self.embedding_provider = embedding_provider
        self.retrieval_chunk_repository = retrieval_chunk_repository
        self.retrieval_graph_repository = retrieval_graph_repository
        self.graph_extractor = graph_extractor
        self.runtime_configuration_service = runtime_configuration_service
        self.default_tenant_id = default_tenant_id

    async def run_import_job_for_file(
        self,
        *,
        file_id: str,
        tenant_id: str,
    ) -> DuplicateIndexImportResult:
        download_duration, download_result = await self._timed(
            "file_download",
            self.file_service_client.download_file,
            file_id,
            tenant_id,
            fallback_file_name=file_id,
        )
        file_path, file_name, content_type = download_result
        try:
            parse_duration, parsed_rows = await self._timed(
                "csv_parse",
                self.file_service_client.parse_csv_rows,
                file_path,
            )
            field_config = await self.runtime_configuration_service.get_field_config()
            prepared_rows = self._build_chunk_rows(
                tenant_id=tenant_id,
                file_id=file_id,
                file_name=file_name,
                content_type=content_type,
                parsed_rows=parsed_rows,
                embedding_fields=field_config.embedding_fields,
            )
            chunk_rows = [prepared.chunk_row for prepared in prepared_rows]
            embedding_duration, embeddings = await self._timed(
                "embedding_generation",
                self.embedding_provider.embed_texts,
                [str(chunk["content"]) for chunk in chunk_rows],
            )
            for chunk, embedding in zip(chunk_rows, embeddings, strict=False):
                chunk["embedding"] = embedding

            graph_duration, document_graphs = await self._timed(
                "graph_rebuild",
                self._build_document_graphs,
                prepared_rows,
                field_config.graph_entity_fields,
            )
            upsert_duration, _ = await self._timed(
                "chunk_upsert",
                self.retrieval_chunk_repository.replace_file_chunks,
                tenant_id,
                file_id,
                [prepared.document_row for prepared in prepared_rows],
                chunk_rows,
            )
            await self._timed(
                "graph_upsert",
                self.retrieval_graph_repository.replace_file_graphs,
                tenant_id=tenant_id,
                file_id=file_id,
                document_graphs=document_graphs,
            )

            metadata = {
                "file_name": file_name,
                "row_count": len(chunk_rows),
                "download_ms": download_duration,
                "parse_ms": parse_duration,
                "embedding_ms": embedding_duration,
                "upsert_ms": upsert_duration,
                "graph_ms": graph_duration,
            }
            await self.retrieval_chunk_repository.upsert_file_sync_state(
                tenant_id,
                {
                    "file_id": file_id,
                    "source_updated_at": None,
                    "last_indexed_at": datetime.now(UTC),
                    "parse_status": "indexed",
                    "content_hash": self.file_service_client.content_hash(file_path),
                    "metadata": metadata,
                },
            )
            return DuplicateIndexImportResult(
                file_id=file_id,
                row_count=len(chunk_rows),
                metadata=metadata,
            )
        except Exception as exc:
            await self.retrieval_chunk_repository.upsert_file_sync_state(
                tenant_id,
                {
                    "file_id": file_id,
                    "source_updated_at": None,
                    "last_indexed_at": datetime.now(UTC),
                    "parse_status": "failed",
                    "content_hash": self.file_service_client.content_hash(file_path)
                    if Path(file_path).exists()
                    else "",
                    "metadata": {
                        "file_name": file_name,
                        "error_message": str(exc),
                    },
                },
            )
            raise
        finally:
            Path(file_path).unlink(missing_ok=True)

    @staticmethod
    def _normalize_text(value: object) -> str:
        return " ".join(str(value).strip().lower().split())

    def _build_chunk_rows(
        self,
        *,
        tenant_id: str,
        file_id: str,
        file_name: str,
        content_type: str,
        parsed_rows: list[dict[str, str]],
        embedding_fields: tuple[str, ...] = (),
    ) -> list[PreparedChunkRow]:
        prepared_rows: list[PreparedChunkRow] = []
        for row_number, row in enumerate(parsed_rows, start=1):
            fields = {
                str(source_header): str(value).strip()
                for source_header, value in row.items()
                if str(source_header).strip() and str(value).strip()
            }
            if not fields:
                continue
            # SA-1451: embedded text is composed from the configured embedding
            # fields only (empty selection -> every field, sorted, as before).
            # fallback_to_all_when_empty: a row that carries none of the
            # configured fields still embeds its whole content rather than an
            # empty (useless) vector.
            embedded_field_names = resolve_included_fields(
                sorted(fields), embedding_fields, fallback_to_all_when_empty=True
            )
            content = compose_field_text(
                fields, embedded_field_names, separator=" | "
            )
            document_id = self._document_id(file_id, row_number)
            chunk_id = self._chunk_id(document_id)
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            metadata = {
                "file_id": file_id,
                "file_name": file_name,
                "row_number": row_number,
                "fields": fields,
                "raw_headers": list(row.keys()),
                "embedding_fields": list(embedded_field_names),
            }
            searchable_fields = [
                {
                    "field_name": self._normalize_text(field_name),
                    "field_value": field_value,
                    "normalized_field_value": self._normalize_text(field_value),
                    "row_number": row_number,
                }
                for field_name, field_value in fields.items()
                if self._normalize_text(field_name) and self._normalize_text(field_value)
            ]
            prepared_rows.append(
                PreparedChunkRow(
                    document_row={
                        "document_id": document_id,
                        "source_file_id": file_id,
                        "content_hash": content_hash,
                        "status": "indexed",
                        "doc_type": "structured_row",
                        "mime_type": content_type or "text/csv",
                        "filename": file_name,
                        "metadata": metadata,
                    },
                    chunk_row={
                        "chunk_id": chunk_id,
                        "tenant_id": tenant_id,
                        "document_id": document_id,
                        "source_file_id": file_id,
                        "content_hash": content_hash,
                        "status": "indexed",
                        "doc_type": "structured_row",
                        "mime_type": content_type or "text/csv",
                        "row_number": row_number,
                        "chunk_kind": "structured_row",
                        "content_format": "text",
                        "content": content,
                        "searchable_fields": searchable_fields,
                        "metadata": metadata,
                    },
                )
            )
        return prepared_rows

    async def _build_document_graphs(
        self,
        prepared_rows: list[PreparedChunkRow],
        graph_entity_fields: tuple[str, ...] = (),
    ) -> list[tuple[str, object]]:
        document_graphs: list[tuple[str, object]] = []
        for prepared in prepared_rows:
            document_id = str(prepared.document_row["document_id"])
            chunk_id = str(prepared.chunk_row["chunk_id"])
            # SA-1451: graph entities are extracted from the configured
            # graph-entity fields only. Empty selection -> the full row text
            # (identical to the pre-SA-1451 behavior). Composed from the row's
            # complete field map so a graph selection is independent of which
            # fields were embedded.
            fields = dict(prepared.chunk_row["metadata"].get("fields", {}))
            graph_field_names = resolve_included_fields(
                sorted(fields), graph_entity_fields
            )
            graph_text = compose_field_text(
                fields, graph_field_names, separator=" | "
            )
            extraction_result = self.graph_extractor.extract(
                document_id=document_id,
                chunks=[GraphSourceChunk(chunk_id=chunk_id, text=graph_text)],
            )
            document_graphs.append((document_id, extraction_result))
        return document_graphs

    @staticmethod
    def _document_id(file_id: str, row_number: int) -> str:
        return hashlib.sha256(f"{file_id}:{row_number}".encode("utf-8")).hexdigest()[:64]

    @staticmethod
    def _chunk_id(document_id: str) -> str:
        return f"{document_id}:row"

    async def _timed(self, step_name: str, operation, *args, **kwargs):
        started_at = asyncio.get_running_loop().time()
        result = operation(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        elapsed_ms = round((asyncio.get_running_loop().time() - started_at) * 1000, 3)
        self.logger.info(
            "Retrieval import performance",
            step=step_name,
            duration_ms=elapsed_ms,
        )
        return elapsed_ms, result
