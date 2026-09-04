from __future__ import annotations

import bm25s

from smart_service_sdk.layer1_domain.entities.retrieval_candidate import RetrievalCandidate


class Bm25sLexicalReranker:
    async def rerank(
        self, query_text: str, candidates: list[RetrievalCandidate]
    ) -> list[RetrievalCandidate]:
        if not candidates:
            return []

        corpus = [self._content_for_reranking(candidate) for candidate in candidates]
        retriever = bm25s.BM25()
        retriever.index(bm25s.tokenize(corpus, return_ids=False, show_progress=False))
        ranked_indices, scores = retriever.retrieve(
            bm25s.tokenize(query_text, return_ids=False, show_progress=False),
            k=len(candidates),
            return_as="tuple",
            show_progress=False,
        )

        scored_candidates = [
            RetrievalCandidate(
                document_id=candidates[index].document_id,
                chunk_id=candidates[index].chunk_id,
                content=candidates[index].content,
                score=float(score),
                route=candidates[index].route,
                metadata=dict(candidates[index].metadata),
            )
            for index, score in zip(ranked_indices[0], scores[0], strict=False)
        ]
        return scored_candidates

    @staticmethod
    def _content_for_reranking(candidate: RetrievalCandidate) -> str:
        semantic_content = candidate.metadata.get(
            "semantic_content"
        ) or candidate.metadata.get("semantic_text")
        if isinstance(semantic_content, str) and semantic_content.strip():
            return semantic_content
        return candidate.content
