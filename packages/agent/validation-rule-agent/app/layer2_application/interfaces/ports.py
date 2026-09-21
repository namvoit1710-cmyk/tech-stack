"""Layer-2 ports (interfaces) for the Validation Rule Agent.

Plain-Python structural interfaces (typing.Protocol) — no framework, no `Any`.
The dynamic external payloads (rule steps, data-factory / LLM JSON) are typed
`dict[str, object]` at that boundary.
"""
from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.template_field import TemplateField


class FileServicePort(Protocol):
    def download(self, file_key: str) -> tuple[bytes, str]: ...

    def convert_to_csv_and_upload(
        self, file_bytes: bytes, extension: str
    ) -> tuple[str, bytes, str] | None: ...

    def fetch_preview_file_data(self, file_id: str) -> dict[str, object]: ...


class HeaderInferencePort(Protocol):
    async def get_header_by_llm(self, preview_text: str) -> dict[str, object]: ...


class PlannerPort(Protocol):
    async def plan(self, user_prompt: str, header: dict[str, object] | None) -> dict[str, object]: ...


class ColumnExecutorPort(Protocol):
    def execute_column_operations(
        self, file_path: str, file_format: str, rules: list[dict[str, object]]
    ) -> dict[str, object]: ...

    def execute_batch(
        self, current_file: str, file_format: str, steps: list[dict[str, object]]
    ) -> dict[str, object]: ...


class SummaryPort(Protocol):
    async def generate_summary(self, result: dict[str, object]) -> str: ...


class GovernancePort(Protocol):
    def get_template_fields(self, template_id: str) -> list[TemplateField]: ...


class PolarGeneratorPort(Protocol):
    async def generate(self, message: str, header: dict[str, object] | None = None) -> dict[str, object]: ...
