from typing import Protocol

from smart_service_sdk.layer1_domain.entities.parsed_text_document import ParsedTextDocument


class ITextDocumentParser(Protocol):
    async def parse(self, file_path: str) -> ParsedTextDocument: ...
