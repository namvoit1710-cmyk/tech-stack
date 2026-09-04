from hashlib import sha256

from smart_service_sdk.layer1_domain.entities.parsed_text_document import ParsedTextDocument
from smart_service_sdk.layer2_application.interfaces.chunking_interface import ITextChunker


class TextChunker(ITextChunker):
    def __init__(self, max_characters: int = 4000):
        self._max_characters = max(1, int(max_characters))

    def chunk(
        self,
        parsed_document: ParsedTextDocument,
        document_id: str,
        max_characters: int | None = None,
    ) -> list[dict[str, object]]:
        split_contents = self._split_content(
            parsed_document.content, max_characters=max_characters
        )
        if not split_contents:
            return []

        parent_context_id = self._parent_context_id(
            document_id=document_id,
            content=parsed_document.content,
        )
        sibling_count = len(split_contents)
        chunks: list[dict[str, object]] = []
        for index, content in enumerate(split_contents, start=1):
            anchor_label = f"text-{index}"
            chunks.append(
                {
                    "chunk_id": f"{document_id}-text-{index}",
                    "content": content,
                    "metadata": {
                        **parsed_document.metadata,
                        "content_format": parsed_document.content_format,
                        "parent_context_id": parent_context_id,
                        "sibling_index": index,
                        "sibling_count": sibling_count,
                        "anchor_label": anchor_label,
                        "source_label": anchor_label,
                        "section_title": self._section_title(content, anchor_label),
                    },
                }
            )
        return chunks

    @staticmethod
    def _parent_context_id(document_id: str, content: str) -> str:
        normalized_content = content.strip()
        digest = sha256(
            f"{document_id}:text:{normalized_content}".encode("utf-8")
        ).hexdigest()
        return f"parent-{digest[:16]}"

    def _split_content(
        self, content: str, max_characters: int | None = None
    ) -> list[str]:
        normalized = content.strip()
        if not normalized:
            return []

        max_chunk_characters = self._effective_max_characters(max_characters)
        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            end = min(start + max_chunk_characters, len(normalized))
            if end < len(normalized):
                split_index = normalized.rfind("\n\n", start, end)
                if split_index <= start:
                    split_index = normalized.rfind("\n", start, end)
                if split_index > start:
                    end = split_index

            piece = normalized[start:end].strip()
            if piece:
                chunks.append(piece)

            start = end
            while start < len(normalized) and normalized[start] == "\n":
                start += 1

        return chunks

    def _effective_max_characters(self, max_characters: int | None = None) -> int:
        if max_characters is None:
            return self._max_characters
        return max(1, int(max_characters))

    @staticmethod
    def _section_title(content: str, anchor_label: str) -> str:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        for line in lines:
            if line.startswith("#"):
                return line.lstrip("#").strip() or anchor_label
        for line in lines:
            if len(line) < 120:
                return line
        return anchor_label
