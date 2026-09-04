from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GraphSourceChunk:
    chunk_id: str
    text: str


@dataclass(frozen=True)
class ExtractedGraphEntity:
    entity_id: str
    canonical_name: str
    entity_type: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractedGraphRelation:
    source_entity_id: str
    target_entity_id: str
    relation_type: str
    confidence: float
    evidence_chunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractedGraphMention:
    entity_id: str
    chunk_id: str
    surface_text: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class GraphExtractionResult:
    entities: tuple[ExtractedGraphEntity, ...] = ()
    relations: tuple[ExtractedGraphRelation, ...] = ()
    mentions: tuple[ExtractedGraphMention, ...] = ()


class IGraphExtractor(Protocol):
    def extract(
        self,
        *,
        document_id: str,
        chunks: list[GraphSourceChunk],
    ) -> GraphExtractionResult: ...
