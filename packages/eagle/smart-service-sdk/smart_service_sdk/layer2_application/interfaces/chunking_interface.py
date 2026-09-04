from __future__ import annotations

from typing import Protocol

from smart_service_sdk.layer1_domain.entities.parsed_text_document import ParsedTextDocument
from smart_service_sdk.layer1_domain.entities.spreadsheet_table import SpreadsheetTableProjection
from smart_service_sdk.layer2_application.interfaces.pdf_text_extractor_interface import (
    PdfExtractionResult,
)


class ITextChunker(Protocol):
    def chunk(
        self,
        parsed_document: ParsedTextDocument,
        document_id: str,
        max_characters: int | None = None,
    ) -> list[dict[str, object]]: ...


class IPdfChunker(Protocol):
    def chunk(
        self,
        extraction: PdfExtractionResult,
        document_id: str,
        max_characters: int | None = None,
    ) -> list[dict[str, object]]: ...


class ISpreadsheetSemanticChunker(Protocol):
    def chunk(
        self,
        projections: list[SpreadsheetTableProjection],
    ) -> list[dict[str, object]]: ...
