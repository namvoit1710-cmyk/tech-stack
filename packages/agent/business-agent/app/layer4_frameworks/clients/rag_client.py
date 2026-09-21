from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Optional

import httpx

from app.layer1_domain.entities.rag_context import (
    CreateKnowledgeBaseRequest,
    IngestedDocument,
    IngestionRequest,
    KnowledgeBase,
    RagCandidate,
    RagQueryRequest,
    RagQueryResponse,
    IngestionUploadResponse,
    UploadIngestionRequest,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.rag_client_interface import IRagClient


class RagClient(IRagClient):
    def __init__(
        self,
        *,
        base_url: str,
        query_path: str,
        tenant_id: str,
        top_k: int,
        timeout_seconds: float,
        logger: ILogger,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._query_path = query_path if query_path.startswith("/") else f"/{query_path}"
        self._tenant_id = tenant_id
        self._top_k = top_k
        self._timeout_seconds = timeout_seconds
        self._logger = logger

    async def query_candidates(
        self,
        *,
        query_text: str,
        knowledge_base_ids: Optional[list[str]] = None,
        document_ids: Optional[list[str]] = None,
    ) -> list[RagCandidate]:
        if not self._base_url:
            self._logger.info("RAG API URL is not configured. Skipping RAG retrieval.")
            return []

        normalized_query = query_text.strip()
        if not normalized_query:
            return []

        normalized_kb_ids = [
            kb_id.strip()
            for kb_id in knowledge_base_ids
            if isinstance(kb_id, str) and kb_id.strip()
        ] if knowledge_base_ids else None

        normalized_document_ids = [
            doc_id.strip()
            for doc_id in document_ids
            if isinstance(doc_id, str) and doc_id.strip()
        ] if document_ids else None


        request = RagQueryRequest(
            tenant_id=self._tenant_id,
            query_text=normalized_query,
            knowledge_base_ids=normalized_kb_ids or [],
            document_ids=normalized_document_ids or [],
            top_k=self._top_k,
        )

        url = f"{self._base_url}{self._query_path}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(url, json=asdict(request))
                response.raise_for_status()

            parsed_response = self._parse_response(response.json())
            return parsed_response.candidates

        except Exception as exc:
            self._logger.warning(
                "RAG retrieval failed. Continuing without RAG context.",
                error=str(exc),
                url=url,
            )
            return []

    def _parse_response(self, payload: object) -> RagQueryResponse:
        if not isinstance(payload, Mapping):
            self._logger.warning(
                "RAG retrieval returned non-object payload",
                payload_type=type(payload).__name__,
            )
            return RagQueryResponse()

        raw_candidates = payload.get("candidates", [])
        if not isinstance(raw_candidates, Sequence) or isinstance(
            raw_candidates, (str, bytes, bytearray)
        ):
            self._logger.warning(
                "RAG retrieval candidates field is not a list",
                candidates_type=type(raw_candidates).__name__,
            )
            return RagQueryResponse()

        candidates = [
            self._parse_candidate(candidate)
            for candidate in raw_candidates
            if isinstance(candidate, Mapping)
        ]

        return RagQueryResponse(candidates=candidates)

    def _parse_candidate(self, payload: Mapping[object, object]) -> RagCandidate:
        document_id = self._optional_str(payload.get("document_id"))
        chunk_id = self._optional_str(payload.get("chunk_id"))
        content = self._str_or_default(payload.get("content"))
        score = self._optional_float(payload.get("score"))
        route = self._optional_str(payload.get("route"))
        metadata = self._dict_str_object(payload.get("metadata"))

        known_keys = {
            "document_id",
            "chunk_id",
            "content",
            "score",
            "route",
            "metadata",
        }
        extra = {
            key: value
            for key, value in payload.items()
            if isinstance(key, str) and key not in known_keys
        }

        return RagCandidate(
            document_id=document_id,
            chunk_id=chunk_id,
            content=content,
            score=score,
            route=route,
            metadata=metadata,
            extra=extra,
        )

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _str_or_default(value: object, default: str = "") -> str:
        return value if isinstance(value, str) else default

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            return float(value)
        return None

    @staticmethod
    def _dict_str_object(value: object) -> dict[str, object]:
        if not isinstance(value, Mapping):
            return {}
        return {
            key: item
            for key, item in value.items()
            if isinstance(key, str)
        }

    async def get_knowledge_bases(self) -> list[KnowledgeBase]:
        if not self._base_url:
            self._logger.info(
                "RAG API URL is not configured. Skipping knowledge base retrieval."
            )
            return []

        url = f"{self._base_url}/api/v1/knowledge-bases"

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(
                    url,
                    params={"tenant_id": self._tenant_id},
                )
                response.raise_for_status()

            return [KnowledgeBase.from_dict(item) for item in response.json()]

        except Exception as exc:
            self._logger.warning(
                "Failed to retrieve knowledge bases.",
                error=str(exc),
                url=url,
            )
            return []

    async def get_knowledge_base_by_name(self, name: str) -> Optional[KnowledgeBase]:
        normalized_name = name.strip()
        if not normalized_name:
            self._logger.info(
                "Knowledge base name must not be empty.",
                name=normalized_name,
            )

        knowledge_bases = await self.get_knowledge_bases()

        matches = [kb for kb in knowledge_bases if kb.name.strip() == normalized_name]

        if matches:
            return matches[0]
        self._logger.info(
            "No knowledge base found with the specified name.",
            name=normalized_name,
        )
        return None

    async def create_knowledge_base(
        self,
        request: CreateKnowledgeBaseRequest,
    ) -> Optional[KnowledgeBase]:
        
        if not self._base_url:
            self._logger.info(
                "RAG API URL is not configured. Skipping knowledge base creation."
            )
        url = f"{self._base_url}/api/v1/knowledge-bases"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(url, json=asdict(request))
                response.raise_for_status()

            return KnowledgeBase.from_dict(response.json())

        except Exception as exc:
            self._logger.warning(
                "Failed to create knowledge base.",
                error=str(exc),
                url=url,
            )
            return None


    async def upload_ingestion(
    self,
    request: UploadIngestionRequest,
) -> Optional[IngestionUploadResponse]:
        if not self._base_url:
            self._logger.info("RAG API URL is not configured. Skipping upload.")
            raise RuntimeError("Base URL not configured")

        url = f"{self._base_url}/api/v1/ingestions/upload"

        try:
            form_data = {
                "tenant_id": request.tenant_id,
                "metadata": request.metadata or "",
                "chunk_size_characters": str(request.chunk_size_characters),
                "tags": request.tags or "",
                "source_hints": request.source_hints or "",
            }

            files = {
                "file": (request.file),
            }

            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    url,
                    data=form_data,
                    files=files,
                    headers={"accept": "application/json"},
                )
                response.raise_for_status()

            return IngestionUploadResponse.from_dict(response.json())

        except Exception as exc:
            self._logger.warning(
                "Ingestion upload failed.",
                error=str(exc),
                url=url,
            )
            return None
        

    async def ingestions(
    self,
    request: IngestionRequest,
) -> Optional[IngestionUploadResponse]:
        if not self._base_url:
            self._logger.info("RAG API URL is not configured. Skipping upload.")
            raise RuntimeError("Base URL not configured")

        url = f"{self._base_url}/api/v1/ingestions"

        try:
            
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    url,
                    json=asdict(request)
                )
                response.raise_for_status()

            return IngestionUploadResponse.from_dict(response.json())

        except Exception as exc:
            self._logger.warning(
                "Ingestion upload failed.",
                error=str(exc),
                url=url,
            )
            return None
        
    async def get_knowledge_bases_by_document_id(self, document_id: str) -> Optional[IngestedDocument]:
        if not self._base_url:
            self._logger.info(
                "RAG API URL is not configured. Skipping knowledge base retrieval."
            )
            return None

        url = f"{self._base_url}/api/v1/documents/{document_id}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(
                    url
                )
                response.raise_for_status()

            return IngestedDocument.from_dict(response.json())

        except Exception as exc:
            self._logger.warning(
                "Failed to retrieve knowledge bases.",
                error=str(exc),
                url=url,
            )
            return None