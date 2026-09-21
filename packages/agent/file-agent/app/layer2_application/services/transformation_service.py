from __future__ import annotations

from typing import Any, Protocol

from app.layer1_domain.entities.transformation import TransformationExecutionResult


class TransformationService(Protocol):
    def execute(
        self,
        file_id: str,
        rules: list[dict[str, Any]],
    ) -> TransformationExecutionResult: ...