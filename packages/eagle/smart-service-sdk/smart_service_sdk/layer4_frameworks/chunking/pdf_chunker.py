from __future__ import annotations

from hashlib import sha256

from smart_service_sdk.layer2_application.interfaces.chunking_interface import IPdfChunker
from smart_service_sdk.layer2_application.interfaces.pdf_text_extractor_interface import (
    PdfDocumentBlock,
    PdfExtractionResult,
)


class PdfChunker(IPdfChunker):
    def __init__(self, max_characters: int = 1200, overlap_characters: int = 150):
        self._max_characters = max_characters
        self._overlap_characters = overlap_characters

    def chunk(
        self,
        extraction: PdfExtractionResult,
        document_id: str,
        max_characters: int | None = None,
    ) -> list[dict[str, object]]:
        max_chunk_characters = self._effective_max_characters(max_characters)
        chunks: list[dict[str, object]] = []
        for page in extraction.pages:
            for block in sorted(page.blocks, key=lambda item: item.reading_order):
                block_chunks = self._chunk_block(page, block, max_chunk_characters)
                if not block_chunks:
                    continue

                parent_context_id = self._parent_context_id(
                    document_id, page.page_number, block
                )
                sibling_count = len(block_chunks)
                for index, content in enumerate(block_chunks, start=1):
                    digest = sha256(
                        f"{document_id}:{page.page_number}:{block.block_id}:{block.block_type}:{content}".encode(
                            "utf-8"
                        )
                    ).hexdigest()
                    anchor_label = f"{block.block_id}:part:{index}"
                    metadata = {
                        "block_id": block.block_id,
                        "block_type": block.block_type,
                        "parser": extraction.metadata.get("parser", "unknown"),
                        "parent_context_id": parent_context_id,
                        "sibling_index": index,
                        "sibling_count": sibling_count,
                        "anchor_label": anchor_label,
                        "source_label": f"Page {page.page_number}",
                        "section_title": self._section_title(
                            block, content, anchor_label
                        ),
                    }
                    if block.block_type == "table":
                        metadata["table_headers"] = self._table_headers_for_block(
                            page, block
                        )
                    chunks.append(
                        {
                            "chunk_id": f"pdf-{digest[:16]}",
                            "document_id": document_id,
                            "page_no": page.page_number,
                            "content": content,
                            "metadata": metadata,
                        }
                    )
        return chunks

    @staticmethod
    def _section_title(
        block: PdfDocumentBlock,
        content: str,
        anchor_label: str,
    ) -> str:
        if block.block_type == "table":
            return anchor_label
        for line in content.splitlines():
            candidate = line.strip()
            if candidate and len(candidate) < 120:
                return candidate
        return anchor_label

    @staticmethod
    def _parent_context_id(
        document_id: str,
        page_number: int,
        block: PdfDocumentBlock,
    ) -> str:
        digest = sha256(
            f"{document_id}:pdf:{page_number}:{block.block_id}:{block.block_type}".encode(
                "utf-8"
            )
        ).hexdigest()
        return f"parent-{digest[:16]}"

    @staticmethod
    def _table_headers_for_block(page, block: PdfDocumentBlock) -> list[str]:
        headers = block.metadata.get("headers")
        if isinstance(headers, list) and headers:
            return [str(header) for header in headers]

        for table in getattr(page, "tables", []):
            if table.table_id == block.block_id:
                return [str(header) for header in table.headers]
        return []

    @staticmethod
    def _table_for_block(page, block: PdfDocumentBlock):
        for table in getattr(page, "tables", []):
            if table.table_id == block.block_id:
                return table
        return None

    def _chunk_block(
        self, page, block: PdfDocumentBlock, max_characters: int
    ) -> list[str]:
        if block.block_type == "table":
            return self._chunk_table(page, block, max_characters)

        lines = [line.strip() for line in block.content.splitlines() if line.strip()]
        if not lines:
            return []
        full_text = "\n".join(lines)
        return self._chunk_text(full_text, max_characters)

    def _chunk_table(
        self, page, block: PdfDocumentBlock, max_characters: int
    ) -> list[str]:
        table_content = block.content.strip()
        if not table_content:
            return []
        if len(table_content) <= max_characters:
            return [table_content]

        table = self._table_for_block(page, block)
        if table is not None:
            header_line = " | ".join(table.headers).strip()
            row_lines = [
                " | ".join(str(value) for value in row).strip() for row in table.rows
            ]
        else:
            lines = [
                line.strip() for line in table_content.splitlines() if line.strip()
            ]
            if not lines:
                return []
            header_line, row_lines = lines[0], lines[1:]

        if not header_line or len(header_line) > max_characters:
            return self._chunk_text(table_content, max_characters)

        chunks: list[str] = []
        current_rows: list[str] = []
        for row_line in row_lines:
            candidate_lines = [header_line, *current_rows, row_line]
            candidate = "\n".join(candidate_lines).strip()
            if len(candidate) <= max_characters:
                current_rows.append(row_line)
                continue

            if current_rows:
                chunks.append("\n".join([header_line, *current_rows]).strip())
                current_rows = []

            single_row_chunk = "\n".join([header_line, row_line]).strip()
            if len(single_row_chunk) <= max_characters:
                current_rows.append(row_line)
                continue

            chunks.extend(self._chunk_text(single_row_chunk, max_characters))

        if current_rows:
            chunks.append("\n".join([header_line, *current_rows]).strip())

        return chunks or self._chunk_text(table_content, max_characters)

    def _chunk_text(self, text: str, max_characters: int | None = None) -> list[str]:
        max_chunk_characters = self._effective_max_characters(max_characters)
        if len(text) <= max_chunk_characters:
            return [text]

        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + max_chunk_characters, len(text))
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            minimum_advance = max(max_chunk_characters // 4, 1)
            start = max(end - self._overlap_characters, start + minimum_advance)
        return chunks

    def _effective_max_characters(self, max_characters: int | None = None) -> int:
        if max_characters is None:
            return max(1, int(self._max_characters))
        return max(1, int(max_characters))
