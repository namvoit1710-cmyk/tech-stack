from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class PdfDocumentBlock:
    block_id: str
    page_number: int
    block_type: str
    content: str
    reading_order: int
    bbox: tuple[float, float, float, float] | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PdfStructuredTable:
    table_id: str
    page_number: int
    headers: list[str]
    rows: list[list[str]]
    summary_text: str
    bbox: tuple[float, float, float, float] | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PdfPageStructure:
    page_number: int
    text: str
    character_count: int
    word_count: int
    blocks: list[PdfDocumentBlock] = field(default_factory=list)
    tables: list[PdfStructuredTable] = field(default_factory=list)
    extraction_notes: list[str] = field(default_factory=list)


PdfPageText = PdfPageStructure


@dataclass(frozen=True)
class PdfExtractionResult:
    page_count: int
    pages: list[PdfPageStructure]
    metadata: dict[str, object] = field(default_factory=dict)


class IPdfTextExtractor(Protocol):
    async def extract_text(self, file_path: str) -> PdfExtractionResult: ...
