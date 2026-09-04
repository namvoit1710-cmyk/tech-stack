from __future__ import annotations

import math
from hashlib import sha256


class SimpleEmbeddingProvider:
    def __init__(self, dimensions: int = 64):
        self._dimensions = max(8, int(dimensions))

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        values = [0.0] * self._dimensions
        tokens = [token for token in str(text).lower().split() if token]
        if not tokens:
            return values
        for token in tokens:
            digest = sha256(token.encode("utf-8")).digest()
            for index in range(self._dimensions):
                values[index] += digest[index % len(digest)] / 255.0
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            return values
        return [value / norm for value in values]
