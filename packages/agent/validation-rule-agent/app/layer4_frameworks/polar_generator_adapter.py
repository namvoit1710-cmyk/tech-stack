"""Layer-4 implementation of PolarGeneratorPort.

Wraps the concrete polar-generation pipeline (LLM scope classify -> row/column
generator) behind the port so Layer 2 stays framework-independent.
"""
from __future__ import annotations

from app.layer4_frameworks.generate_polar_by_scope import generate_polar_by_scope


class PolarGeneratorAdapter:
    async def generate(self, message: str, header: dict[str, object] | None = None) -> dict[str, object]:
        return await generate_polar_by_scope(message, header=header)
