from __future__ import annotations

import asyncio
import json
from pathlib import Path

from smart_service_sdk.layer1_domain.entities.parsed_text_document import ParsedTextDocument


class PlainTextDocumentParser:
    _TEXT_EXTENSIONS = {".md", ".txt"}
    _DECODING_CANDIDATES = (
        "utf-8-sig",
        "utf-8",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "cp932",
        "shift_jis",
        "gb18030",
        "gb2312",
        "big5",
        "cp1252",
        "latin-1",
    )

    async def parse(self, file_path: str) -> ParsedTextDocument:
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParsedTextDocument:
        path = Path(file_path)
        extension = path.suffix.lower()
        raw_content = self._read_text(path)

        if extension == ".json":
            content = json.dumps(
                json.loads(raw_content),
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
            )
            content_format = "json"
        elif extension in self._TEXT_EXTENSIONS:
            content = self._normalize_text(raw_content)
            content_format = "markdown" if extension == ".md" else "text"
        else:
            raise ValueError(
                f"Unsupported plain text document extension: {extension or '<none>'}"
            )

        return ParsedTextDocument(
            content=content,
            content_format=content_format,
            metadata={
                "file_name": path.name,
                "source_extension": extension,
            },
        )

    @classmethod
    def _read_text(cls, path: Path) -> str:
        raw_bytes = path.read_bytes()
        if cls._looks_binary(raw_bytes):
            raise ValueError(
                "Input looks like a binary file, not a plain text document"
            )

        last_error: UnicodeDecodeError | None = None
        for encoding in cls._DECODING_CANDIDATES:
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError as exc:
                last_error = exc

        if last_error is not None:
            raise ValueError(f"Unable to decode text file: {path.name}") from last_error
        return ""

    @staticmethod
    def _normalize_text(content: str) -> str:
        return content.replace("\r\n", "\n").replace("\r", "\n")

    @staticmethod
    def _looks_binary(raw_bytes: bytes) -> bool:
        if not raw_bytes:
            return False
        if b"\x00" in raw_bytes:
            return True
        sample = raw_bytes[:4096]
        control_count = sum(
            1 for byte in sample if byte < 32 and byte not in {9, 10, 12, 13}
        )
        return control_count / max(len(sample), 1) > 0.30
