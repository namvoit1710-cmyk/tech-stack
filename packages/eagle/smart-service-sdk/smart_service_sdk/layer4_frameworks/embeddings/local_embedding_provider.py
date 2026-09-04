from __future__ import annotations

import asyncio
from typing import Any, cast


class LocalEmbeddingProvider:
    def __init__(
        self,
        model_name: str,
        query_instruction: str,
        batch_size: int = 32,
        max_input_characters: int = 4000,
    ):
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if max_input_characters < 1:
            raise ValueError("max_input_characters must be >= 1")
        self._query_instruction = query_instruction.strip()
        self._batch_size = batch_size
        self._max_input_characters = max_input_characters
        self._model = self._load_model(model_name)

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        bounded_texts = [self._bound_text(text) for text in texts]
        for start in range(0, len(bounded_texts), self._batch_size):
            batch = bounded_texts[start : start + self._batch_size]
            batch_vectors = await asyncio.to_thread(
                self._model.encode, batch, normalize_embeddings=True
            )
            for vector in cast(Any, batch_vectors):
                vectors.append([float(value) for value in vector])
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        prefixed_text = self._bound_text(f"{self._query_instruction} {text}".strip())
        vectors = await asyncio.to_thread(
            self._model.encode, [prefixed_text], normalize_embeddings=True
        )
        first_vector = cast(Any, vectors)[0]
        return [float(value) for value in first_vector]
    
    async def _init_model(self, model_name: str) -> None:
        self._model = await asyncio.to_thread(self._load_model, model_name)

    def _load_model(self, model_name: str):
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(model_name)

    def _bound_text(self, text: str) -> str:
        if len(text) <= self._max_input_characters:
            return text
        return text[: self._max_input_characters]
