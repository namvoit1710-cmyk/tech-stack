from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedTextDocument:
    content: str
    content_format: str
    metadata: dict[str, object]
