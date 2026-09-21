from typing import Optional, Protocol

from app.layer1_domain.entities.rag_context import CreateKnowledgeBaseRequest, IngestedDocument, IngestionRequest, IngestionUploadResponse, KnowledgeBase, RagCandidate, UploadIngestionRequest


class IRagClient(Protocol):
    async def query_candidates(
        self,
        *,
        query_text: str,
        knowledge_base_ids: Optional[list[str]] = None,
        document_ids: Optional[list[str]] = None,
    ) -> list[RagCandidate]: ...

    async def get_knowledge_bases(self) -> list[KnowledgeBase]: ...

    async def get_knowledge_base_by_name(self, name: str) -> Optional[KnowledgeBase]: ...

    async def create_knowledge_base(
        self, request: CreateKnowledgeBaseRequest
    ) -> Optional[KnowledgeBase]: ...

    async def upload_ingestion(
        self,
        request: UploadIngestionRequest,
    ) -> Optional[IngestionUploadResponse]: ...

    async def ingestions(
        self,
        request: IngestionRequest,
    ) -> Optional[IngestionUploadResponse]: ...

    async def get_knowledge_bases_by_document_id(self, document_id: str) -> Optional[IngestedDocument]:...
