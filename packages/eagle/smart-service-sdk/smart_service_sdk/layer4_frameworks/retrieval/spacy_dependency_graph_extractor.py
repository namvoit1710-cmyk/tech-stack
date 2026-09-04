from __future__ import annotations

import re
import threading
from hashlib import sha256
from typing import Any

from smart_service_sdk.layer2_application.interfaces.graph_extractor_interface import (
    ExtractedGraphEntity,
    ExtractedGraphMention,
    ExtractedGraphRelation,
    GraphExtractionResult,
    GraphSourceChunk,
    IGraphExtractor,
)
from smart_service_sdk.layer4_frameworks.retrieval.spacy_model_loader import (
    load_spacy_model,
)


class _SingleTokenSpan:
    def __init__(self, token: Any):
        self._token = token
        self.label_ = ""
        self.start = int(token.i)
        self.end = int(token.i) + 1
        self.root = token
        self.text = str(token.text)

    def __iter__(self):
        return iter((self._token,))


class _StructuredFieldSpan:
    def __init__(
        self,
        *,
        text: str,
        label: str,
        start_offset: int,
        end_offset: int,
    ) -> None:
        self.text = text
        self.label_ = label
        self.start = start_offset
        self.end = end_offset
        self.identity_key = SpacyDependencyGraphExtractor._structured_identity_key(
            label,
            text,
        )

    def __iter__(self):
        return iter(())


class SpacyDependencyGraphExtractor(IGraphExtractor):
    _NORMALIZE_PATTERN = re.compile(r"[^a-z0-9]+")

    def __init__(
        self,
        *,
        spacy_model_name: str = "en_core_web_sm",
        nlp: Any | None = None,
        min_relation_confidence: float = 0.5,
        max_entities_per_document: int = 64,
        max_relations_per_document: int = 128,
    ):
        self._spacy_model_name = spacy_model_name
        self._nlp = nlp
        self._lock = threading.Lock()
        self._min_relation_confidence = min_relation_confidence
        self._max_entities_per_document = max_entities_per_document
        self._max_relations_per_document = max_relations_per_document

    def extract(
        self,
        *,
        document_id: str,
        chunks: list[GraphSourceChunk],
    ) -> GraphExtractionResult:
        entities_by_id: dict[str, ExtractedGraphEntity] = {}
        relations_by_key: dict[tuple[str, str, str], ExtractedGraphRelation] = {}
        mentions: list[ExtractedGraphMention] = []
        mention_keys: set[tuple[str, str, int, int]] = set()
        nlp = None

        for chunk in chunks:
            structured_spans = self._structured_field_spans(chunk.text)
            if structured_spans:
                self._append_mentions(
                    document_id=document_id,
                    chunk_id=chunk.chunk_id,
                    entity_spans=structured_spans,
                    entities_by_id=entities_by_id,
                    mention_keys=mention_keys,
                    mentions=mentions,
                )
                continue

            if nlp is None:
                nlp = self._ensure_nlp()
            doc = nlp(chunk.text)
            entity_spans = self._candidate_entity_spans(doc)
            self._append_mentions(
                document_id=document_id,
                chunk_id=chunk.chunk_id,
                entity_spans=entity_spans,
                entities_by_id=entities_by_id,
                mention_keys=mention_keys,
                mentions=mentions,
            )

            span_by_token_index = self._span_by_token_index(entity_spans)
            for relation_candidate in self._relation_candidates(
                doc, span_by_token_index
            ):
                if relation_candidate["confidence"] < self._min_relation_confidence:
                    continue
                source_entity = self._ensure_entity(
                    document_id,
                    relation_candidate["source_span"],
                    entities_by_id,
                )
                target_entity = self._ensure_entity(
                    document_id,
                    relation_candidate["target_span"],
                    entities_by_id,
                )
                if source_entity is None or target_entity is None:
                    continue
                relation_key = (
                    source_entity.entity_id,
                    relation_candidate["relation_type"],
                    target_entity.entity_id,
                )
                existing_relation = relations_by_key.get(relation_key)
                if existing_relation is None:
                    if len(relations_by_key) >= self._max_relations_per_document:
                        continue
                    relations_by_key[relation_key] = ExtractedGraphRelation(
                        source_entity_id=source_entity.entity_id,
                        target_entity_id=target_entity.entity_id,
                        relation_type=relation_candidate["relation_type"],
                        confidence=relation_candidate["confidence"],
                        evidence_chunk_ids=(chunk.chunk_id,),
                    )
                    continue

                evidence_chunk_ids = tuple(
                    dict.fromkeys(
                        (*existing_relation.evidence_chunk_ids, chunk.chunk_id)
                    )
                )
                relations_by_key[relation_key] = ExtractedGraphRelation(
                    source_entity_id=existing_relation.source_entity_id,
                    target_entity_id=existing_relation.target_entity_id,
                    relation_type=existing_relation.relation_type,
                    confidence=max(
                        existing_relation.confidence,
                        relation_candidate["confidence"],
                    ),
                    evidence_chunk_ids=evidence_chunk_ids,
                )

        return GraphExtractionResult(
            entities=tuple(entities_by_id.values()),
            relations=tuple(relations_by_key.values()),
            mentions=tuple(mentions),
        )

    def _ensure_nlp(self):
        if self._nlp is None:
            with self._lock:
                if self._nlp is None:
                    self._nlp = load_spacy_model(self._spacy_model_name)
        return self._nlp

    def _append_mentions(
        self,
        *,
        document_id: str,
        chunk_id: str,
        entity_spans: list[Any],
        entities_by_id: dict[str, ExtractedGraphEntity],
        mention_keys: set[tuple[str, str, int, int]],
        mentions: list[ExtractedGraphMention],
    ) -> None:
        for span in entity_spans:
            entity = self._ensure_entity(document_id, span, entities_by_id)
            if entity is None:
                continue
            mention_key = (entity.entity_id, chunk_id, int(span.start), int(span.end))
            if mention_key in mention_keys:
                continue
            mention_keys.add(mention_key)
            mentions.append(
                ExtractedGraphMention(
                    entity_id=entity.entity_id,
                    chunk_id=chunk_id,
                    surface_text=str(span.text),
                    start_offset=int(span.start),
                    end_offset=int(span.end),
                )
            )

    @classmethod
    def _canonical_key(cls, text: str) -> str:
        normalized = cls._NORMALIZE_PATTERN.sub(" ", text.lower()).strip()
        return normalized.replace(" ", "-")

    @classmethod
    def _structured_identity_key(cls, entity_type: str, canonical_name: str) -> str:
        return (
            f"{cls._canonical_key(entity_type)}::{cls._canonical_key(canonical_name)}"
        )

    @classmethod
    def _relation_type(cls, token: Any) -> str:
        raw_lemma = getattr(token, "lemma_", "") or getattr(token, "text", "")
        normalized = cls._NORMALIZE_PATTERN.sub("_", str(raw_lemma).lower()).strip("_")
        return normalized or "related_to"

    def _candidate_entity_spans(self, doc: Any) -> list[Any]:
        spans: list[Any] = []
        seen = set()

        for span in tuple(getattr(doc, "ents", ())):
            canonical_key = self._canonical_key(span.text)
            if not canonical_key:
                continue
            span_key = (span.start, span.end, canonical_key)
            if span_key in seen:
                continue
            seen.add(span_key)
            spans.append(span)

        try:
            noun_chunks = tuple(getattr(doc, "noun_chunks", ()))
        except Exception:
            noun_chunks = ()
        for span in noun_chunks:
            canonical_key = self._canonical_key(span.text)
            if not canonical_key:
                continue
            span_key = (span.start, span.end, canonical_key)
            if span_key in seen:
                continue
            seen.add(span_key)
            spans.append(span)

        return spans

    def _structured_field_spans(self, text: str) -> list[_StructuredFieldSpan]:
        spans: list[_StructuredFieldSpan] = []
        seen: set[tuple[int, int, str]] = set()
        source_text = str(text)
        search_offset = 0

        for raw_segment in source_text.split("|"):
            segment_start = source_text.find(raw_segment, search_offset)
            if segment_start < 0:
                continue
            segment_end = segment_start + len(raw_segment)
            search_offset = segment_end + 1
            segment = raw_segment.strip()
            if not segment or ":" not in segment:
                continue

            raw_key, raw_value = segment.split(":", 1)
            entity_type = " ".join(raw_key.split()).strip()
            canonical_name = " ".join(raw_value.split()).strip()
            if not entity_type or not canonical_name:
                continue

            value_start_in_segment = raw_segment.find(raw_value)
            if value_start_in_segment < 0:
                value_start_in_segment = raw_segment.find(canonical_name)
            if value_start_in_segment < 0:
                continue

            start_offset = segment_start + value_start_in_segment
            while start_offset < len(source_text) and source_text[start_offset].isspace():
                start_offset += 1
            end_offset = start_offset + len(canonical_name)
            span_key = (
                start_offset,
                end_offset,
                self._structured_identity_key(entity_type, canonical_name),
            )
            if span_key in seen:
                continue
            seen.add(span_key)
            spans.append(
                _StructuredFieldSpan(
                    text=canonical_name,
                    label=entity_type,
                    start_offset=start_offset,
                    end_offset=end_offset,
                )
            )

        return spans

    @staticmethod
    def _span_by_token_index(entity_spans: list[Any]) -> dict[int, Any]:
        span_by_token_index: dict[int, Any] = {}
        for span in entity_spans:
            for token in span:
                span_by_token_index.setdefault(int(token.i), span)
        return span_by_token_index

    def _ensure_entity(
        self,
        document_id: str,
        span: Any,
        entities_by_id: dict[str, ExtractedGraphEntity],
    ) -> ExtractedGraphEntity | None:
        entity_identity = str(
            getattr(span, "identity_key", self._canonical_key(str(span.text)))
        )
        if not entity_identity:
            return None
        entity_id = self._entity_id(document_id, entity_identity)
        existing = entities_by_id.get(entity_id)
        if existing is not None:
            return existing
        if len(entities_by_id) >= self._max_entities_per_document:
            return None
        entity = ExtractedGraphEntity(
            entity_id=entity_id,
            canonical_name=str(span.text),
            entity_type=str(getattr(span, "label_", "") or "NOUN_PHRASE"),
        )
        entities_by_id[entity_id] = entity
        return entity

    @staticmethod
    def _entity_id(document_id: str, entity_identity: str) -> str:
        return sha256(f"{document_id}|{entity_identity}".encode("utf-8")).hexdigest()[:64]

    def _relation_candidates(
        self, doc: Any, span_by_token_index: dict[int, Any]
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for token in doc:
            if getattr(token, "pos_", "") != "VERB":
                continue

            active_source = self._child_with_deps(token, {"nsubj", "csubj"})
            active_target = self._child_with_deps(
                token, {"dobj", "attr", "oprd", "dative"}
            )
            passive_target = self._child_with_deps(token, {"nsubjpass"})
            passive_source = self._passive_source(token)

            if passive_source is not None and passive_target is not None:
                candidates.append(
                    {
                        "source_span": self._resolve_span(
                            passive_source, span_by_token_index
                        ),
                        "target_span": self._resolve_span(
                            passive_target, span_by_token_index
                        ),
                        "relation_type": self._relation_type(token),
                        "confidence": 0.95,
                    }
                )
                continue

            if active_source is not None and active_target is not None:
                candidates.append(
                    {
                        "source_span": self._resolve_span(
                            active_source, span_by_token_index
                        ),
                        "target_span": self._resolve_span(
                            active_target, span_by_token_index
                        ),
                        "relation_type": self._relation_type(token),
                        "confidence": 0.9,
                    }
                )
                continue

            fallback = self._linear_fallback(token, span_by_token_index)
            if fallback is not None:
                candidates.append(fallback)

        return candidates

    @staticmethod
    def _child_with_deps(token: Any, deps: set[str]) -> Any | None:
        for child in token.children:
            if getattr(child, "dep_", "") in deps:
                return child
        return None

    def _passive_source(self, token: Any) -> Any | None:
        for child in token.children:
            if getattr(child, "dep_", "") != "agent":
                continue
            for grandchild in child.children:
                if getattr(grandchild, "dep_", "") == "pobj":
                    return grandchild
        return None

    def _resolve_span(self, token: Any, span_by_token_index: dict[int, Any]) -> Any:
        return span_by_token_index.get(int(token.i), _SingleTokenSpan(token))

    def _linear_fallback(
        self, token: Any, span_by_token_index: dict[int, Any]
    ) -> dict[str, Any] | None:
        unique_spans = list(dict.fromkeys(span_by_token_index.values()))
        left_spans = [span for span in unique_spans if int(span.end) <= int(token.i)]
        right_spans = [span for span in unique_spans if int(span.start) > int(token.i)]
        if not left_spans or not right_spans:
            return None
        return {
            "source_span": left_spans[-1],
            "target_span": right_spans[0],
            "relation_type": self._relation_type(token),
            "confidence": 0.4,
        }


__all__ = ["SpacyDependencyGraphExtractor"]
