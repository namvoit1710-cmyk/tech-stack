from __future__ import annotations

from typing import Any, Protocol


class IStructuredLLMGateway(Protocol):
    async def generate_execution_plan(
        self, *, system_prompt: str, user_prompt: str
    ) -> dict[str, Any]:
        """Return a plain execution-plan dictionary."""

    async def generate_column_transform_payload(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        verification_prompt: str,
        file_id: str,
    ) -> dict[str, Any]:
        """Return a plain Data Factory column-transform payload."""

    async def generate_row_transform_payload(
        self, *, system_prompt: str, user_prompt: str, file_id: str
    ) -> dict[str, Any]:
        """Return a plain Data Factory row-transform payload."""
