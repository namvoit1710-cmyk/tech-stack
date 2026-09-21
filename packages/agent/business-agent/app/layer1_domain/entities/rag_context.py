from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, BinaryIO


@dataclass(frozen=True, slots=True)
class RagCandidate:
    document_id: str | None = None
    chunk_id: str | None = None
    content: str = ""
    score: float | None = None
    route: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RagQueryRequest:
    tenant_id: str
    query_text: str
    knowledge_base_ids: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    top_k: int = 10


@dataclass(frozen=True, slots=True)
class RagQueryResponse:
    candidates: list[RagCandidate] = field(default_factory=list)


@dataclass
class KnowledgeBase:
    knowledge_base_id: str
    tenant_id: str
    name: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeBase":
        return cls(
            knowledge_base_id=data["knowledge_base_id"],
            tenant_id=data["tenant_id"],
            name=data.get("name", ""),
            description=data.get("description", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class CreateKnowledgeBaseRequest:
    tenant_id: str
    name: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UploadIngestionRequest:
    tenant_id: str
    file: bytes

    metadata: str | None = None
    chunk_size_characters: int = 800
    tags: str | None = None
    source_hints: str | None = None
    knowledge_base_ids: list[str] | None = field(default_factory=list)


@dataclass(frozen=True)
class IngestedDocument:
    document_id: str
    tenant_id: str
    file_id: str
    filename: str
    media_type: str
    ingestion_status: str
    metadata: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IngestedDocument":
        return cls(
            document_id=data["document_id"],
            tenant_id=data["tenant_id"],
            file_id=data["file_id"],
            filename=data["filename"],
            media_type=data["media_type"],
            ingestion_status=data["ingestion_status"],
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class IngestionJob:
    job_id: str
    tenant_id: str
    document_id: str
    status: str
    error_message: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IngestionJob":
        return cls(
            job_id=data["job_id"],
            tenant_id=data["tenant_id"],
            document_id=data["document_id"],
            status=data["status"],
            error_message=data.get("error_message"),
        )


@dataclass(frozen=True)
class IngestionUploadResponse:
    document: IngestedDocument
    job: IngestionJob
    created: bool
    duplicate_reason: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IngestionUploadResponse":
        return cls(
            document=IngestedDocument.from_dict(data["document"]),
            job=IngestionJob.from_dict(data["job"]),
            created=data["created"],
            duplicate_reason=data.get("duplicate_reason"),
        )


@dataclass(frozen=True)
class IngestionRequest:
    tenant_id: str
    file_id: str
    filename: str | None = None
    media_type: str = "text/csv"
    content_hash: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    chunk_size_characters: int = 800

    tags: list[str] = field(default_factory=list)

    source_hints: dict[str, Any] = field(default_factory=dict)
    source_guide: dict[str, Any] = field(default_factory=dict)

    knowledge_base_ids: list[str] = field(default_factory=list)

    allowed_roles: list[str] = field(default_factory=list)
