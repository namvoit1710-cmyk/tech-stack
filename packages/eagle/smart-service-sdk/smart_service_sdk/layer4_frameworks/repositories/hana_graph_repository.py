from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from hashlib import sha256
from typing import Any, cast

from smart_service_sdk.layer1_domain.entities.retrieval_candidate import RetrievalCandidate
from smart_service_sdk.layer1_domain.entities.retrieval_query import RetrievalRoute
from smart_service_sdk.layer2_application.interfaces.graph_extractor_interface import (
    ExtractedGraphEntity,
    ExtractedGraphMention,
    ExtractedGraphRelation,
    GraphExtractionResult,
)
from smart_service_sdk.layer4_frameworks.repositories.json_repository_codec import JsonRepositoryCodec

_JSON_CODEC = JsonRepositoryCodec()


class HanaGraphRepository:
    _NORMALIZE_PATTERN = re.compile(r"[^a-z0-9]+")
    _UPSERT_GRAPH_ENTITIES_SQL = (
        "UPSERT AE_RAG_GRAPH_ENTITIES (ENTITY_ID, TENANT_ID, CANONICAL_NAME, ENTITY_TYPE, METADATA_JSON) "
        "VALUES (?, ?, ?, ?, ?) WITH PRIMARY KEY"
    )
    _INSERT_GRAPH_RELATIONS_SQL = (
        "INSERT INTO AE_RAG_GRAPH_RELATIONS (RELATION_ID, TENANT_ID, SOURCE_ENTITY_ID, TARGET_ENTITY_ID, RELATION_TYPE, CONFIDENCE, METADATA_JSON) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    _INSERT_GRAPH_MENTIONS_SQL = (
        "INSERT INTO AE_RAG_GRAPH_MENTIONS (MENTION_ID, TENANT_ID, ENTITY_ID, CHUNK_ID, SURFACE_TEXT, START_OFFSET, END_OFFSET, METADATA_JSON) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )

    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    @staticmethod
    def _json_value(value: object) -> str | None:
        return str(value) if value is not None else None

    @staticmethod
    def _safe_float(value: object, default: float = 0.0) -> float:
        try:
            return float(cast(Any, value))
        except (TypeError, ValueError):
            return default

    def _entity_insert_rows(
        self,
        *,
        tenant_id: str,
        document_graphs: list[tuple[str, GraphExtractionResult]],
    ) -> list[tuple[object, ...]]:
        rows: list[tuple[object, ...]] = []
        for _document_id, extraction_result in document_graphs:
            for entity in extraction_result.entities:
                rows.append(
                    (
                        entity.entity_id,
                        tenant_id,
                        entity.canonical_name,
                        entity.entity_type,
                        _JSON_CODEC.dump_json(self._entity_metadata(entity)),
                    )
                )
        return rows

    def _relation_insert_rows(
        self,
        *,
        tenant_id: str,
        file_id: str,
        document_graphs: list[tuple[str, GraphExtractionResult]],
    ) -> list[tuple[object, ...]]:
        rows: list[tuple[object, ...]] = []
        for document_id, extraction_result in document_graphs:
            for relation in extraction_result.relations:
                rows.append(
                    (
                        self._relation_id(tenant_id, document_id, relation),
                        tenant_id,
                        relation.source_entity_id,
                        relation.target_entity_id,
                        relation.relation_type,
                        relation.confidence,
                        _JSON_CODEC.dump_json(
                            self._relation_metadata(
                                document_id,
                                relation,
                                source_file_id=file_id,
                            )
                        ),
                    )
                )
        return rows

    def _mention_insert_rows(
        self,
        *,
        tenant_id: str,
        file_id: str,
        document_graphs: list[tuple[str, GraphExtractionResult]],
    ) -> list[tuple[object, ...]]:
        rows: list[tuple[object, ...]] = []
        for document_id, extraction_result in document_graphs:
            for mention in extraction_result.mentions:
                rows.append(
                    (
                        self._mention_id(tenant_id, document_id, mention),
                        tenant_id,
                        mention.entity_id,
                        mention.chunk_id,
                        mention.surface_text,
                        mention.start_offset,
                        mention.end_offset,
                        _JSON_CODEC.dump_json(
                            {
                                "document_id": document_id,
                                "source_file_id": file_id,
                            }
                        ),
                    )
                )
        return rows

    async def replace_document_graph(
        self,
        tenant_id: str,
        document_id: str,
        extraction_result: GraphExtractionResult,
    ) -> None:
        def _replace() -> None:
            connection = self._connection_factory.acquire()
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT DISTINCT AE_RAG_GRAPH_MENTIONS.ENTITY_ID "
                    "FROM AE_RAG_GRAPH_MENTIONS JOIN AE_RAG_CHUNKS "
                    "ON AE_RAG_CHUNKS.TENANT_ID = AE_RAG_GRAPH_MENTIONS.TENANT_ID "
                    "AND AE_RAG_CHUNKS.CHUNK_ID = AE_RAG_GRAPH_MENTIONS.CHUNK_ID "
                    "WHERE AE_RAG_GRAPH_MENTIONS.TENANT_ID = ? AND AE_RAG_CHUNKS.DOCUMENT_ID = ?",
                    (tenant_id, document_id),
                )
                stale_entity_ids = [str(row[0]) for row in cursor.fetchall() if row and row[0]]

                cursor.execute(
                    "DELETE FROM AE_RAG_GRAPH_MENTIONS "
                    "WHERE TENANT_ID = ? AND EXISTS ("
                    "SELECT 1 FROM AE_RAG_CHUNKS "
                    "WHERE AE_RAG_CHUNKS.TENANT_ID = AE_RAG_GRAPH_MENTIONS.TENANT_ID "
                    "AND AE_RAG_CHUNKS.CHUNK_ID = AE_RAG_GRAPH_MENTIONS.CHUNK_ID "
                    "AND AE_RAG_CHUNKS.DOCUMENT_ID = ?"
                    ")",
                    (tenant_id, document_id),
                )
                cursor.execute(
                    "DELETE FROM AE_RAG_GRAPH_RELATIONS "
                    "WHERE TENANT_ID = ? AND METADATA_JSON LIKE ?",
                    (tenant_id, f'%\"document_id\": \"{document_id}\"%'),
                )
                if stale_entity_ids:
                    placeholders = ", ".join("?" for _ in stale_entity_ids)
                    cursor.execute(
                        "DELETE FROM AE_RAG_GRAPH_ENTITIES "
                        f"WHERE TENANT_ID = ? AND ENTITY_ID IN ({placeholders}) "
                        "AND NOT EXISTS ("
                        "SELECT 1 FROM AE_RAG_GRAPH_MENTIONS "
                        "WHERE AE_RAG_GRAPH_MENTIONS.TENANT_ID = AE_RAG_GRAPH_ENTITIES.TENANT_ID "
                        "AND AE_RAG_GRAPH_MENTIONS.ENTITY_ID = AE_RAG_GRAPH_ENTITIES.ENTITY_ID"
                        ")",
                        (tenant_id, *stale_entity_ids),
                    )

                for entity in extraction_result.entities:
                    cursor.execute(
                        "UPSERT AE_RAG_GRAPH_ENTITIES (ENTITY_ID, TENANT_ID, CANONICAL_NAME, ENTITY_TYPE, METADATA_JSON) VALUES (?, ?, ?, ?, ?) WITH PRIMARY KEY",
                        (
                            entity.entity_id,
                            tenant_id,
                            entity.canonical_name,
                            entity.entity_type,
                            _JSON_CODEC.dump_json(self._entity_metadata(entity)),
                        ),
                    )

                for relation in extraction_result.relations:
                    cursor.execute(
                        "INSERT INTO AE_RAG_GRAPH_RELATIONS (RELATION_ID, TENANT_ID, SOURCE_ENTITY_ID, TARGET_ENTITY_ID, RELATION_TYPE, CONFIDENCE, METADATA_JSON) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            self._relation_id(tenant_id, document_id, relation),
                            tenant_id,
                            relation.source_entity_id,
                            relation.target_entity_id,
                            relation.relation_type,
                            relation.confidence,
                            _JSON_CODEC.dump_json(
                                self._relation_metadata(document_id, relation)
                            ),
                        ),
                    )

                for mention in extraction_result.mentions:
                    cursor.execute(
                        "INSERT INTO AE_RAG_GRAPH_MENTIONS (MENTION_ID, TENANT_ID, ENTITY_ID, CHUNK_ID, SURFACE_TEXT, START_OFFSET, END_OFFSET, METADATA_JSON) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            self._mention_id(tenant_id, document_id, mention),
                            tenant_id,
                            mention.entity_id,
                            mention.chunk_id,
                            mention.surface_text,
                            mention.start_offset,
                            mention.end_offset,
                            "{}",
                        ),
                    )

                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
                self._connection_factory.release(connection)

        await asyncio.to_thread(_replace)

    async def replace_file_graphs(
        self,
        *,
        tenant_id: str,
        file_id: str,
        document_graphs: list[tuple[str, GraphExtractionResult]],
    ) -> None:
        def _replace() -> None:
            connection = self._connection_factory.acquire()
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT DISTINCT ENTITY_ID FROM AE_RAG_GRAPH_MENTIONS "
                    "WHERE TENANT_ID = ? AND JSON_VALUE(METADATA_JSON, '$.source_file_id') = ?",
                    (tenant_id, file_id),
                )
                stale_entity_ids = [str(row[0]) for row in cursor.fetchall() if row and row[0]]

                cursor.execute(
                    "DELETE FROM AE_RAG_GRAPH_MENTIONS "
                    "WHERE TENANT_ID = ? AND JSON_VALUE(METADATA_JSON, '$.source_file_id') = ?",
                    (tenant_id, file_id),
                )
                cursor.execute(
                    "DELETE FROM AE_RAG_GRAPH_RELATIONS "
                    "WHERE TENANT_ID = ? AND JSON_VALUE(METADATA_JSON, '$.source_file_id') = ?",
                    (tenant_id, file_id),
                )
                if stale_entity_ids:
                    placeholders = ", ".join("?" for _ in stale_entity_ids)
                    cursor.execute(
                        "DELETE FROM AE_RAG_GRAPH_ENTITIES "
                        f"WHERE TENANT_ID = ? AND ENTITY_ID IN ({placeholders}) "
                        "AND NOT EXISTS ("
                        "SELECT 1 FROM AE_RAG_GRAPH_MENTIONS "
                        "WHERE AE_RAG_GRAPH_MENTIONS.TENANT_ID = AE_RAG_GRAPH_ENTITIES.TENANT_ID "
                        "AND AE_RAG_GRAPH_MENTIONS.ENTITY_ID = AE_RAG_GRAPH_ENTITIES.ENTITY_ID"
                        ")",
                        (tenant_id, *stale_entity_ids),
                    )

                entity_rows = self._entity_insert_rows(
                    tenant_id=tenant_id,
                    document_graphs=document_graphs,
                )
                relation_rows = self._relation_insert_rows(
                    tenant_id=tenant_id,
                    file_id=file_id,
                    document_graphs=document_graphs,
                )
                mention_rows = self._mention_insert_rows(
                    tenant_id=tenant_id,
                    file_id=file_id,
                    document_graphs=document_graphs,
                )

                if entity_rows:
                    cursor.executemany(self._UPSERT_GRAPH_ENTITIES_SQL, entity_rows)
                if relation_rows:
                    cursor.executemany(self._INSERT_GRAPH_RELATIONS_SQL, relation_rows)
                if mention_rows:
                    cursor.executemany(self._INSERT_GRAPH_MENTIONS_SQL, mention_rows)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
                self._connection_factory.release(connection)

        await asyncio.to_thread(_replace)

    async def find_graph_candidates(
        self,
        *,
        tenant_id: str,
        query_embedding: list[float],
        seed_texts: list[str],
        filters: dict[str, object] | None,
        seed_top_k: int,
        neighbor_cap_per_seed: int,
        max_relation_candidates: int,
        max_graph_chunk_candidates: int,
        max_hops: int,
    ) -> list[RetrievalCandidate]:
        resolved_filters = filters or {}
        allowed_document_ids = resolved_filters.get("allowed_document_ids")
        if isinstance(allowed_document_ids, list) and not allowed_document_ids:
            return []
        if not seed_texts or max_hops < 1:
            return []

        def _find() -> list[RetrievalCandidate]:
            connection = self._connection_factory.acquire()
            cursor = connection.cursor()
            try:
                user_roles = self._user_roles(resolved_filters)
                seed_entities = self._find_seed_entities(
                    cursor,
                    tenant_id=tenant_id,
                    seed_texts=seed_texts,
                    filters=resolved_filters,
                    seed_top_k=seed_top_k,
                )
                if not seed_entities:
                    return []

                seed_entity_ids = [str(entity["entity_id"]) for entity in seed_entities]
                relations = self._find_one_hop_relations(
                    cursor,
                    tenant_id=tenant_id,
                    seed_entity_ids=seed_entity_ids,
                    neighbor_cap_per_seed=neighbor_cap_per_seed,
                    max_relation_candidates=max_relation_candidates,
                )
                relation_paths_by_entity_id = self._relation_paths_by_entity_id(
                    relations
                )
                relation_scores_by_entity_id = self._relation_scores_by_entity_id(
                    relations
                )
                mention_rows = self._find_chunk_mentions(
                    cursor,
                    tenant_id=tenant_id,
                    entity_ids=self._candidate_entity_ids(seed_entity_ids, relations),
                    filters=resolved_filters,
                )
                if not mention_rows:
                    return []

                candidates_by_chunk_id: dict[str, RetrievalCandidate] = {}
                chunk_scores: dict[str, float] = defaultdict(float)
                evidence_by_chunk_id: dict[str, dict[str, object]] = {}

                seed_entity_id_lookup = set(seed_entity_ids)
                for row in mention_rows:
                    chunk_id = str(row[1])
                    entity_id = str(row[10])
                    entity_name = str(row[11])
                    contribution = (
                        1.0
                        if entity_id in seed_entity_id_lookup
                        else max(
                            relation_scores_by_entity_id.get(entity_id, 0.0),
                            0.25,
                        )
                    )
                    chunk_scores[chunk_id] += contribution
                    evidence = evidence_by_chunk_id.setdefault(
                        chunk_id,
                        {
                            "seed_texts": list(seed_texts),
                            "matched_entities": [],
                            "relation_paths": [],
                        },
                    )
                    matched_entities = cast(list[str], evidence["matched_entities"])
                    if entity_name not in matched_entities:
                        matched_entities.append(entity_name)
                    relation_paths = cast(
                        list[dict[str, object]], evidence["relation_paths"]
                    )
                    for relation_path in relation_paths_by_entity_id.get(entity_id, ()):
                        if relation_path not in relation_paths:
                            relation_paths.append(relation_path)

                    metadata = _JSON_CODEC.load_json_object(self._json_value(row[7]))
                    if not self._metadata_allows_roles(metadata, user_roles):
                        continue
                    metadata["page_no"] = row[3]
                    metadata["table_id"] = row[4]
                    metadata["chunk_kind"] = row[5]
                    metadata["content_format"] = row[6]
                    parent_context_id = row[8]
                    parent_context_content = row[9]
                    if (
                        parent_context_id is not None
                        and parent_context_content is not None
                    ):
                        parent_context: dict[str, object] = {
                            "parent_context_id": str(parent_context_id),
                            "summary_text": str(parent_context_content),
                        }
                        anchor_label = metadata.get("anchor_label")
                        if anchor_label is not None:
                            parent_context["anchor_label"] = anchor_label
                        sibling_index = metadata.get("sibling_index")
                        sibling_count = metadata.get("sibling_count")
                        if sibling_index is not None and sibling_count is not None:
                            parent_context["sibling_span"] = {
                                "index": sibling_index,
                                "count": sibling_count,
                            }
                        metadata["parent_context"] = parent_context
                    metadata["graph_evidence"] = evidence
                    candidates_by_chunk_id[chunk_id] = RetrievalCandidate(
                        document_id=str(row[0]),
                        chunk_id=chunk_id,
                        content=str(row[2]),
                        score=0.0,
                        route=RetrievalRoute.HYBRID_DOCUMENT,
                        metadata=metadata,
                    )

                ranked_candidates: list[RetrievalCandidate] = []
                for chunk_id, candidate in candidates_by_chunk_id.items():
                    ranked_candidates.append(
                        RetrievalCandidate(
                            document_id=candidate.document_id,
                            chunk_id=candidate.chunk_id,
                            content=candidate.content,
                            score=chunk_scores.get(chunk_id, 0.0),
                            route=candidate.route,
                            metadata=dict(candidate.metadata),
                        )
                    )

                ranked_candidates.sort(
                    key=lambda candidate: candidate.score, reverse=True
                )
                return ranked_candidates[:max_graph_chunk_candidates]
            finally:
                cursor.close()
                self._connection_factory.release(connection)

        return await asyncio.to_thread(_find)

    @staticmethod
    def _entity_metadata(entity: ExtractedGraphEntity) -> dict[str, object]:
        if entity.aliases:
            return {"aliases": list(entity.aliases)}
        return {}

    @staticmethod
    def _relation_metadata(
        document_id: str,
        relation: ExtractedGraphRelation,
        *,
        source_file_id: str | None = None,
    ) -> dict[str, object]:
        metadata: dict[str, object] = {"document_id": document_id}
        if source_file_id:
            metadata["source_file_id"] = source_file_id
        if relation.evidence_chunk_ids:
            metadata["evidence_chunk_ids"] = list(relation.evidence_chunk_ids)
        return metadata

    @staticmethod
    def _relation_id(
        tenant_id: str,
        document_id: str,
        relation: ExtractedGraphRelation,
    ) -> str:
        return sha256(
            "|".join(
                [
                    tenant_id,
                    document_id,
                    relation.source_entity_id,
                    relation.relation_type,
                    relation.target_entity_id,
                ]
            ).encode("utf-8")
        ).hexdigest()[:64]

    @staticmethod
    def _mention_id(
        tenant_id: str,
        document_id: str,
        mention: ExtractedGraphMention,
    ) -> str:
        return sha256(
            "|".join(
                [
                    tenant_id,
                    document_id,
                    mention.entity_id,
                    mention.chunk_id,
                    str(mention.start_offset),
                    str(mention.end_offset),
                    mention.surface_text,
                ]
            ).encode("utf-8")
        ).hexdigest()[:64]

    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return cls._NORMALIZE_PATTERN.sub(" ", value.lower()).strip()

    def _find_seed_entities(
        self,
        cursor,
        *,
        tenant_id: str,
        seed_texts: list[str],
        filters: dict[str, object],
        seed_top_k: int,
    ) -> list[dict[str, object]]:
        normalized_seed_texts = [
            self._normalize_text(seed_text)
            for seed_text in seed_texts
            if self._normalize_text(seed_text)
        ]
        if not normalized_seed_texts:
            return []

        sql = (
            "SELECT AE_RAG_GRAPH_ENTITIES.ENTITY_ID, AE_RAG_GRAPH_ENTITIES.CANONICAL_NAME, "
            "AE_RAG_GRAPH_ENTITIES.METADATA_JSON "
            "FROM AE_RAG_GRAPH_ENTITIES "
            "WHERE AE_RAG_GRAPH_ENTITIES.TENANT_ID = ? "
        )
        params: list[object] = [tenant_id]
        exists_clauses = [
            "AE_RAG_GRAPH_MENTIONS.TENANT_ID = AE_RAG_GRAPH_ENTITIES.TENANT_ID",
            "AE_RAG_GRAPH_MENTIONS.ENTITY_ID = AE_RAG_GRAPH_ENTITIES.ENTITY_ID",
            "AE_RAG_CHUNKS.TENANT_ID = AE_RAG_GRAPH_MENTIONS.TENANT_ID",
            "AE_RAG_CHUNKS.CHUNK_ID = AE_RAG_GRAPH_MENTIONS.CHUNK_ID",
            "AE_RAG_DOCUMENTS.TENANT_ID = AE_RAG_CHUNKS.TENANT_ID",
            "AE_RAG_DOCUMENTS.DOCUMENT_ID = AE_RAG_CHUNKS.DOCUMENT_ID",
            "AE_RAG_DOCUMENTS.STATUS = 'indexed'",
        ]
        allowed_document_ids = filters.get("allowed_document_ids")
        if isinstance(allowed_document_ids, list) and allowed_document_ids:
            placeholders = ", ".join("?" for _ in allowed_document_ids)
            exists_clauses.append(f"AE_RAG_CHUNKS.DOCUMENT_ID IN ({placeholders})")
            params.extend(allowed_document_ids)

        user_roles = self._user_roles(filters)
        if user_roles:
            role_conditions = []
            for role in user_roles:
                role_conditions.append("AE_RAG_CHUNKS.METADATA_JSON LIKE ?")
                params.append(f'%"{role}"%')
            role_sql = " OR ".join(role_conditions)
            exists_clauses.append(
                "AE_RAG_CHUNKS.STATUS = 'indexed' "
                "AND (JSON_VALUE(AE_RAG_CHUNKS.METADATA_JSON, '$.allowed_roles[0]') IS NULL "
                f"OR ({role_sql}))"
            )
        sql += (
            " AND EXISTS ("
            "SELECT 1 FROM AE_RAG_GRAPH_MENTIONS "
            "JOIN AE_RAG_CHUNKS ON AE_RAG_CHUNKS.TENANT_ID = AE_RAG_GRAPH_MENTIONS.TENANT_ID "
            "AND AE_RAG_CHUNKS.CHUNK_ID = AE_RAG_GRAPH_MENTIONS.CHUNK_ID "
            "JOIN AE_RAG_DOCUMENTS ON AE_RAG_DOCUMENTS.TENANT_ID = AE_RAG_CHUNKS.TENANT_ID "
            "AND AE_RAG_DOCUMENTS.DOCUMENT_ID = AE_RAG_CHUNKS.DOCUMENT_ID "
            "WHERE "
            + " AND ".join(exists_clauses)
            + ")"
        )

        match_conditions = []
        for seed_text in normalized_seed_texts:
            like_value = f"%{seed_text}%"
            match_conditions.append(
                "(LOWER(AE_RAG_GRAPH_ENTITIES.CANONICAL_NAME) LIKE ? OR LOWER(COALESCE(AE_RAG_GRAPH_ENTITIES.METADATA_JSON, '')) LIKE ?)"
            )
            params.extend((like_value, like_value))
        sql += " AND (" + " OR ".join(match_conditions) + ")"
        cursor.execute(sql, tuple(params))

        ranked_entities: list[dict[str, object]] = []
        for row in cursor.fetchall():
            metadata = _JSON_CODEC.load_json_object(row[2])
            candidate_texts = [str(row[1])]
            aliases = metadata.get("aliases")
            if isinstance(aliases, list):
                candidate_texts.extend(str(alias) for alias in aliases)
            match_score = self._match_score(normalized_seed_texts, candidate_texts)
            if match_score <= 0:
                continue
            ranked_entities.append(
                {
                    "entity_id": str(row[0]),
                    "canonical_name": str(row[1]),
                    "match_score": match_score,
                }
            )

        ranked_entities.sort(
            key=lambda entity: (entity["match_score"], -len(entity["canonical_name"])),
            reverse=True,
        )

        seen_entity_ids: set[str] = set()
        limited_entities: list[dict[str, object]] = []
        for entity in ranked_entities:
            entity_id = str(entity["entity_id"])
            if entity_id in seen_entity_ids:
                continue
            seen_entity_ids.add(entity_id)
            limited_entities.append(entity)
            if len(limited_entities) >= seed_top_k:
                break
        return limited_entities

    @classmethod
    def _match_score(
        cls,
        normalized_seed_texts: list[str],
        candidate_texts: list[str],
    ) -> float:
        normalized_candidates = [
            cls._normalize_text(candidate_text)
            for candidate_text in candidate_texts
            if cls._normalize_text(candidate_text)
        ]
        best_score = 0.0
        for seed_text in normalized_seed_texts:
            seed_tokens = set(seed_text.split())
            for candidate_text in normalized_candidates:
                if not candidate_text:
                    continue
                if candidate_text == seed_text:
                    best_score = max(best_score, 1.0)
                    continue
                if seed_text in candidate_text or candidate_text in seed_text:
                    best_score = max(best_score, 0.85)
                    continue
                candidate_tokens = set(candidate_text.split())
                overlap = seed_tokens & candidate_tokens
                if not overlap:
                    continue
                best_score = max(
                    best_score,
                    len(overlap) / max(len(seed_tokens), len(candidate_tokens)),
                )
        return best_score

    def _find_one_hop_relations(
        self,
        cursor,
        *,
        tenant_id: str,
        seed_entity_ids: list[str],
        neighbor_cap_per_seed: int,
        max_relation_candidates: int,
    ) -> list[dict[str, object]]:
        relations: list[dict[str, object]] = []
        seen_relation_ids: set[str] = set()
        for seed_entity_id in seed_entity_ids:
            cursor.execute(
                "SELECT RELATION_ID, SOURCE_ENTITY_ID, TARGET_ENTITY_ID, RELATION_TYPE, CONFIDENCE "
                "FROM AE_RAG_GRAPH_RELATIONS WHERE TENANT_ID = ? "
                "AND (SOURCE_ENTITY_ID = ? OR TARGET_ENTITY_ID = ?) "
                "ORDER BY COALESCE(CONFIDENCE, 0) DESC LIMIT ?",
                (tenant_id, seed_entity_id, seed_entity_id, neighbor_cap_per_seed),
            )
            for row in cursor.fetchall():
                relation_id = str(row[0])
                if relation_id in seen_relation_ids:
                    continue
                seen_relation_ids.add(relation_id)
                relations.append(
                    {
                        "relation_id": relation_id,
                        "source_entity_id": str(row[1]),
                        "target_entity_id": str(row[2]),
                        "relation_type": str(row[3]),
                        "confidence": float(row[4] or 0.0),
                    }
                )
                if len(relations) >= max_relation_candidates:
                    return relations
        return relations

    @staticmethod
    def _candidate_entity_ids(
        seed_entity_ids: list[str], relations: list[dict[str, object]]
    ) -> list[str]:
        entity_ids = list(seed_entity_ids)
        seen_entity_ids = set(seed_entity_ids)
        for relation in relations:
            for entity_id in (
                str(relation["source_entity_id"]),
                str(relation["target_entity_id"]),
            ):
                if entity_id in seen_entity_ids:
                    continue
                seen_entity_ids.add(entity_id)
                entity_ids.append(entity_id)
        return entity_ids

    def _find_chunk_mentions(
        self,
        cursor,
        *,
        tenant_id: str,
        entity_ids: list[str],
        filters: dict[str, object],
    ) -> list[tuple[object, ...]]:
        if not entity_ids:
            return []

        placeholders = ", ".join("?" for _ in entity_ids)
        sql = (
            "SELECT AE_RAG_CHUNKS.DOCUMENT_ID, AE_RAG_CHUNKS.CHUNK_ID, AE_RAG_CHUNKS.CONTENT, AE_RAG_CHUNKS.PAGE_NO, "
            "AE_RAG_CHUNKS.TABLE_ID, AE_RAG_CHUNKS.CHUNK_KIND, AE_RAG_CHUNKS.CONTENT_FORMAT, AE_RAG_CHUNKS.METADATA_JSON, "
            "AE_RAG_CHUNKS.PARENT_CONTEXT_ID, AE_RAG_PARENT_CONTEXTS.CONTENT, AE_RAG_GRAPH_MENTIONS.ENTITY_ID, AE_RAG_GRAPH_ENTITIES.CANONICAL_NAME "
            "FROM AE_RAG_GRAPH_MENTIONS JOIN AE_RAG_GRAPH_ENTITIES "
            "ON AE_RAG_GRAPH_ENTITIES.TENANT_ID = AE_RAG_GRAPH_MENTIONS.TENANT_ID "
            "AND AE_RAG_GRAPH_ENTITIES.ENTITY_ID = AE_RAG_GRAPH_MENTIONS.ENTITY_ID "
            "JOIN AE_RAG_CHUNKS ON AE_RAG_CHUNKS.TENANT_ID = AE_RAG_GRAPH_MENTIONS.TENANT_ID "
            "AND AE_RAG_CHUNKS.CHUNK_ID = AE_RAG_GRAPH_MENTIONS.CHUNK_ID "
            "JOIN AE_RAG_DOCUMENTS ON AE_RAG_DOCUMENTS.TENANT_ID = AE_RAG_CHUNKS.TENANT_ID "
            "AND AE_RAG_DOCUMENTS.DOCUMENT_ID = AE_RAG_CHUNKS.DOCUMENT_ID "
            "AND AE_RAG_DOCUMENTS.STATUS = 'indexed' "
            "LEFT JOIN AE_RAG_PARENT_CONTEXTS ON AE_RAG_PARENT_CONTEXTS.TENANT_ID = AE_RAG_CHUNKS.TENANT_ID "
            "AND AE_RAG_PARENT_CONTEXTS.DOCUMENT_ID = AE_RAG_CHUNKS.DOCUMENT_ID "
            "AND AE_RAG_PARENT_CONTEXTS.PARENT_CONTEXT_ID = AE_RAG_CHUNKS.PARENT_CONTEXT_ID "
            f"WHERE AE_RAG_GRAPH_MENTIONS.TENANT_ID = ? AND AE_RAG_CHUNKS.STATUS = 'indexed' AND AE_RAG_GRAPH_MENTIONS.ENTITY_ID IN ({placeholders})"
        )
        params: list[object] = [tenant_id, *entity_ids]
        user_roles = self._user_roles(filters)
        if user_roles:
            role_conditions = []
            for role in user_roles:
                role_conditions.append("AE_RAG_CHUNKS.METADATA_JSON LIKE ?")
                params.append(f'%"{role}"%')
            role_sql = " OR ".join(role_conditions)
            sql += (
                " AND (JSON_VALUE(AE_RAG_CHUNKS.METADATA_JSON, '$.allowed_roles[0]') IS NULL "
                f"OR ({role_sql}))"
            )

        allowed_document_ids = filters.get("allowed_document_ids")
        if isinstance(allowed_document_ids, list) and allowed_document_ids:
            document_placeholders = ", ".join("?" for _ in allowed_document_ids)
            sql += f" AND AE_RAG_CHUNKS.DOCUMENT_ID IN ({document_placeholders})"
            params.extend(allowed_document_ids)

        cursor.execute(sql, tuple(params))
        return list(cursor.fetchall())

    @staticmethod
    def _relation_paths_by_entity_id(
        relations: list[dict[str, object]],
    ) -> dict[str, tuple[dict[str, object], ...]]:
        relation_paths_by_entity_id: dict[str, list[dict[str, object]]] = defaultdict(
            list
        )
        for relation in relations:
            relation_path: dict[str, object] = {
                "source_entity_id": str(relation["source_entity_id"]),
                "relation_type": str(relation["relation_type"]),
                "target_entity_id": str(relation["target_entity_id"]),
            }
            relation_paths_by_entity_id[str(relation["source_entity_id"])].append(
                relation_path
            )
            relation_paths_by_entity_id[str(relation["target_entity_id"])].append(
                relation_path
            )
        return {
            entity_id: tuple(paths)
            for entity_id, paths in relation_paths_by_entity_id.items()
        }

    @staticmethod
    def _relation_scores_by_entity_id(
        relations: list[dict[str, object]],
    ) -> dict[str, float]:
        scores_by_entity_id: dict[str, float] = {}
        for relation in relations:
            relation_score = max(
                HanaGraphRepository._safe_float(relation.get("confidence"), 0.0), 0.5
            )
            for entity_id in (
                str(relation["source_entity_id"]),
                str(relation["target_entity_id"]),
            ):
                scores_by_entity_id[entity_id] = max(
                    scores_by_entity_id.get(entity_id, 0.0),
                    relation_score,
                )
        return scores_by_entity_id

    @staticmethod
    def _user_roles(filters: dict[str, object]) -> list[str]:
        user_roles = filters.get("user_roles")
        if not isinstance(user_roles, list):
            return []
        return [str(role).strip().lower() for role in user_roles if str(role).strip()]

    @staticmethod
    def _metadata_allows_roles(
        metadata: dict[str, object], user_roles: list[str]
    ) -> bool:
        if not user_roles:
            return True

        raw_allowed_roles = metadata.get("allowed_roles")
        if not isinstance(raw_allowed_roles, list):
            return True

        normalized_allowed_roles = {
            str(role).strip().lower() for role in raw_allowed_roles if str(role).strip()
        }
        if not normalized_allowed_roles:
            return True

        return bool(set(user_roles) & normalized_allowed_roles)
