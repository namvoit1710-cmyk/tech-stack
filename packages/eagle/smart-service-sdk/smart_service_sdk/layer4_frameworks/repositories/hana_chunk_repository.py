import asyncio
import hashlib

from smart_service_sdk.layer1_domain.entities.duplicate_match_rule import DuplicateMatchRule
from smart_service_sdk.layer1_domain.entities.retrieval_candidate import RetrievalCandidate
from smart_service_sdk.layer1_domain.entities.retrieval_query import RetrievalRoute
from smart_service_sdk.layer4_frameworks.repositories.json_repository_codec import JsonRepositoryCodec

_JSON_CODEC = JsonRepositoryCodec()


class HanaChunkRepository:
    _DELETE_PARENT_CONTEXTS_SQL = (
        "DELETE FROM AE_RAG_PARENT_CONTEXTS WHERE TENANT_ID = ? AND DOCUMENT_ID IN ("
        "SELECT DOCUMENT_ID FROM AE_RAG_DOCUMENTS WHERE TENANT_ID = ? AND SOURCE_FILE_ID = ?"
        ")"
    )
    _DELETE_CHUNK_SEARCH_FIELDS_SQL = (
        "DELETE FROM AE_RAG_CHUNK_SEARCH_FIELDS WHERE TENANT_ID = ? AND DOCUMENT_ID IN ("
        "SELECT DOCUMENT_ID FROM AE_RAG_DOCUMENTS WHERE TENANT_ID = ? AND SOURCE_FILE_ID = ?"
        ")"
    )
    _DELETE_CHUNKS_SQL = (
        "DELETE FROM AE_RAG_CHUNKS WHERE TENANT_ID = ? AND SOURCE_FILE_ID = ?"
    )
    _DELETE_DOCUMENTS_SQL = (
        "DELETE FROM AE_RAG_DOCUMENTS WHERE TENANT_ID = ? AND SOURCE_FILE_ID = ?"
    )
    _INSERT_DOCUMENTS_SQL = (
        "INSERT INTO AE_RAG_DOCUMENTS (DOCUMENT_ID, TENANT_ID, SOURCE_FILE_ID, CONTENT_HASH, STATUS, DOC_TYPE, MIME_TYPE, FILENAME, METADATA_JSON) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    _INSERT_PARENT_CONTEXTS_SQL = (
        "INSERT INTO AE_RAG_PARENT_CONTEXTS (PARENT_CONTEXT_ID, TENANT_ID, DOCUMENT_ID, CONTEXT_KIND, CONTENT, METADATA_JSON) "
        "VALUES (?, ?, ?, ?, ?, ?)"
    )
    _INSERT_CHUNKS_SQL = (
        "INSERT INTO AE_RAG_CHUNKS (CHUNK_ID, TENANT_ID, DOCUMENT_ID, SOURCE_FILE_ID, CONTENT_HASH, STATUS, DOC_TYPE, MIME_TYPE, PAGE_NO, SHEET_NAME, ROW_NUMBER, CHUNK_KIND, CONTENT_FORMAT, TABLE_ID, CONTENT, METADATA_JSON, PARENT_CONTEXT_ID, EMBEDDING) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TO_REAL_VECTOR(?))"
    )
    _INSERT_CHUNK_SEARCH_FIELDS_SQL = (
        "INSERT INTO AE_RAG_CHUNK_SEARCH_FIELDS (TENANT_ID, DOCUMENT_ID, CHUNK_ID, ROW_NUMBER, FIELD_NAME, FIELD_VALUE, NORMALIZED_FIELD_VALUE, NORMALIZED_FIELD_HASH) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )

    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    @staticmethod
    def _mapping(value: object) -> dict[str, object]:
        if isinstance(value, dict):
            return {str(key): item for key, item in value.items()}
        return {}

    @staticmethod
    def _embedding(value: object) -> list[object]:
        return list(value) if isinstance(value, list) else []

    @staticmethod
    def _user_roles(filters: dict[str, object]) -> list[str]:
        raw_user_roles = filters.get("user_roles")
        if not isinstance(raw_user_roles, list):
            return []
        return [str(role) for role in raw_user_roles if str(role)]

    @staticmethod
    def _metadata_allows_roles(
        metadata: dict[str, object], user_roles: list[str]
    ) -> bool:
        normalized_user_roles = {
            str(role).strip().lower() for role in user_roles if str(role).strip()
        }
        if not normalized_user_roles:
            return True

        raw_allowed_roles = metadata.get("allowed_roles")
        if not isinstance(raw_allowed_roles, list):
            return True

        normalized_allowed_roles = {
            str(role).strip().lower() for role in raw_allowed_roles if str(role).strip()
        }
        if not normalized_allowed_roles:
            return True

        return bool(normalized_user_roles & normalized_allowed_roles)

    @staticmethod
    def _normalize_text(value: object) -> str:
        return " ".join(str(value).strip().lower().split())

    @staticmethod
    def _parent_context_id(chunk: dict[str, object]) -> str | None:
        raw_parent_context_id = chunk.get("parent_context_id")
        if isinstance(raw_parent_context_id, str) and raw_parent_context_id:
            return raw_parent_context_id
        metadata = HanaChunkRepository._mapping(chunk.get("metadata", {}))
        if metadata:
            metadata_parent_context_id = metadata.get("parent_context_id")
            if (
                isinstance(metadata_parent_context_id, str)
                and metadata_parent_context_id
            ):
                return metadata_parent_context_id
        return None

    @staticmethod
    def _sibling_index(chunk: dict[str, object]) -> int:
        metadata = HanaChunkRepository._mapping(chunk.get("metadata", {}))
        if metadata:
            sibling_index = metadata.get("sibling_index")
            if isinstance(sibling_index, int):
                return sibling_index
        return 0

    @staticmethod
    def _searchable_field_hash(value: object) -> str:
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

    @classmethod
    def _searchable_fields(
        cls,
        chunk: dict[str, object],
    ) -> list[dict[str, object]]:
        raw_searchable_fields = chunk.get("searchable_fields")
        if isinstance(raw_searchable_fields, list):
            rows: list[dict[str, object]] = []
            for item in raw_searchable_fields:
                if not isinstance(item, dict):
                    continue
                field_name = cls._normalize_text(item.get("field_name", ""))
                normalized_field_value = cls._normalize_text(
                    item.get("normalized_field_value", "")
                )
                field_value = str(item.get("field_value", "")).strip()
                if not field_name or not normalized_field_value or not field_value:
                    continue
                rows.append(
                    {
                        "field_name": field_name,
                        "field_value": field_value,
                        "normalized_field_value": normalized_field_value,
                        "normalized_field_hash": cls._searchable_field_hash(
                            normalized_field_value
                        ),
                        "row_number": item.get("row_number"),
                    }
                )
            if rows:
                return rows

        metadata = cls._mapping(chunk.get("metadata", {}))
        fields = cls._mapping(metadata.get("fields", {}))
        row_number = chunk.get("row_number")
        rows = []
        for key, field_value in fields.items():
            field_name = cls._normalize_text(key)
            normalized_field_value = cls._normalize_text(field_value)
            field_value = str(field_value).strip()
            if not field_name or not normalized_field_value or not field_value:
                continue
            rows.append(
                {
                    "field_name": field_name,
                    "field_value": field_value,
                    "normalized_field_value": normalized_field_value,
                    "normalized_field_hash": cls._searchable_field_hash(
                        normalized_field_value
                    ),
                    "row_number": row_number,
                }
            )
        return rows

    @staticmethod
    def _search_result_limit(
        max_results: int,
        *,
        match_count: int,
        user_roles: list[str],
    ) -> int:
        base_limit = max(max_results, 1)
        multiplier = 12 if user_roles else 6
        minimum = 120 if user_roles else 40
        return max(base_limit * max(match_count, 1) * multiplier, minimum, base_limit)

    @staticmethod
    def _query_values_cte(
        rows: list[tuple[object, ...]],
        aliases: tuple[str, ...],
    ) -> tuple[str, list[object]]:
        selects: list[str] = []
        params: list[object] = []
        for row in rows:
            selects.append(
                "SELECT "
                + ", ".join(f"? AS {alias}" for alias in aliases)
                + " FROM DUMMY"
            )
            params.extend(row)
        return " UNION ALL ".join(selects), params

    @staticmethod
    def _match_query_document_filter(
        allowed_document_ids: object,
        *,
        column_name: str,
    ) -> tuple[str, list[object]]:
        if isinstance(allowed_document_ids, list) and allowed_document_ids:
            placeholders = ", ".join("?" for _ in allowed_document_ids)
            return f" AND {column_name} IN ({placeholders})", list(allowed_document_ids)
        return "", []

    def _candidate_from_row(
        self,
        row: tuple[object, ...],
        *,
        route: RetrievalRoute,
    ) -> RetrievalCandidate:
        metadata = _JSON_CODEC.load_json_object(row[7])
        metadata["page_no"] = row[3]
        metadata["table_id"] = row[4]
        metadata["chunk_kind"] = row[5]
        metadata["content_format"] = row[6]
        parent_context_id = row[8]
        parent_context_content = row[9]
        if parent_context_id is not None and parent_context_content is not None:
            metadata["parent_context"] = {
                "parent_context_id": str(parent_context_id),
                "summary_text": str(parent_context_content),
            }
        return RetrievalCandidate(
            document_id=str(row[0]),
            chunk_id=str(row[1]),
            content=str(row[2]),
            score=0.0,
            route=route,
            metadata=metadata,
        )

    def _parent_context_rows(
        self,
        tenant_id: str,
        document_id: str,
        chunks: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        grouped_chunks: dict[str, list[dict[str, object]]] = {}
        for chunk in chunks:
            parent_context_id = self._parent_context_id(chunk)
            if parent_context_id is None:
                continue
            grouped_chunks.setdefault(parent_context_id, []).append(chunk)

        parent_context_rows: list[dict[str, object]] = []
        for parent_context_id, parent_chunks in grouped_chunks.items():
            sorted_chunks = sorted(parent_chunks, key=self._sibling_index)
            parent_context_rows.append(
                {
                    "parent_context_id": parent_context_id,
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "context_kind": str(
                        sorted_chunks[0].get("chunk_kind", "semantic_text")
                    ),
                    "content": "\n\n".join(
                        str(chunk["content"]) for chunk in sorted_chunks
                    ),
                    "metadata": {},
                }
            )
        return parent_context_rows

    @staticmethod
    def _chunks_by_document_id(
        chunks: list[dict[str, object]],
    ) -> dict[str, list[dict[str, object]]]:
        chunks_by_document_id: dict[str, list[dict[str, object]]] = {}
        for chunk in chunks:
            chunks_by_document_id.setdefault(str(chunk["document_id"]), []).append(chunk)
        return chunks_by_document_id

    def _document_insert_rows(
        self,
        tenant_id: str,
        file_id: str,
        documents: list[dict[str, object]],
    ) -> list[tuple[object, ...]]:
        return [
            (
                document["document_id"],
                tenant_id,
                document.get("source_file_id", file_id),
                document.get("content_hash", ""),
                document.get("status", "indexed"),
                document.get("doc_type", "structured_row"),
                document.get("mime_type", "text/csv"),
                document.get("filename", file_id),
                _JSON_CODEC.dump_json(document.get("metadata", {})),
            )
            for document in documents
        ]

    def _parent_context_insert_rows(
        self,
        tenant_id: str,
        documents: list[dict[str, object]],
        chunks_by_document_id: dict[str, list[dict[str, object]]],
    ) -> list[tuple[object, ...]]:
        rows: list[tuple[object, ...]] = []
        for document in documents:
            document_id = str(document["document_id"])
            document_chunks = chunks_by_document_id.get(document_id, [])
            for parent_context in self._parent_context_rows(
                tenant_id,
                document_id,
                document_chunks,
            ):
                rows.append(
                    (
                        parent_context["parent_context_id"],
                        parent_context["tenant_id"],
                        parent_context["document_id"],
                        parent_context["context_kind"],
                        parent_context["content"],
                        _JSON_CODEC.dump_json(parent_context["metadata"]),
                    )
                )
        return rows

    def _chunk_insert_rows(
        self,
        tenant_id: str,
        file_id: str,
        chunks: list[dict[str, object]],
    ) -> list[tuple[object, ...]]:
        return [
            (
                chunk["chunk_id"],
                tenant_id,
                chunk["document_id"],
                chunk.get("source_file_id", file_id),
                chunk["content_hash"],
                chunk.get("status", "indexed"),
                chunk.get("doc_type", "structured_row"),
                chunk.get("mime_type", "text/csv"),
                chunk.get("page_no"),
                chunk.get("sheet_name"),
                chunk.get("row_number"),
                chunk.get("chunk_kind", "semantic_text"),
                chunk.get("content_format", "text"),
                chunk.get("table_id"),
                chunk["content"],
                _JSON_CODEC.dump_json(chunk.get("metadata", {})),
                self._parent_context_id(chunk),
                _JSON_CODEC.dump_vector(self._embedding(chunk.get("embedding"))),
            )
            for chunk in chunks
        ]

    def _chunk_search_field_insert_rows(
        self,
        tenant_id: str,
        chunks: list[dict[str, object]],
    ) -> list[tuple[object, ...]]:
        rows: list[tuple[object, ...]] = []
        for chunk in chunks:
            for searchable_field in self._searchable_fields(chunk):
                rows.append(
                    (
                        tenant_id,
                        chunk["document_id"],
                        chunk["chunk_id"],
                        searchable_field.get("row_number"),
                        searchable_field["field_name"],
                        searchable_field["field_value"],
                        searchable_field["normalized_field_value"],
                        searchable_field["normalized_field_hash"],
                    )
                )
        return rows

    async def replace_file_chunks(
        self,
        tenant_id: str,
        file_id: str,
        documents: list[dict[str, object]],
        chunks: list[dict[str, object]],
    ) -> None:
        def _replace() -> None:
            connection = self._connection_factory.acquire()
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._DELETE_PARENT_CONTEXTS_SQL,
                    (tenant_id, tenant_id, file_id),
                )
                cursor.execute(
                    self._DELETE_CHUNK_SEARCH_FIELDS_SQL,
                    (tenant_id, tenant_id, file_id),
                )
                cursor.execute(self._DELETE_CHUNKS_SQL, (tenant_id, file_id))
                cursor.execute(self._DELETE_DOCUMENTS_SQL, (tenant_id, file_id))

                chunks_by_document_id = self._chunks_by_document_id(chunks)
                document_rows = self._document_insert_rows(tenant_id, file_id, documents)
                parent_context_rows = self._parent_context_insert_rows(
                    tenant_id,
                    documents,
                    chunks_by_document_id,
                )
                chunk_rows = self._chunk_insert_rows(tenant_id, file_id, chunks)
                search_field_rows = self._chunk_search_field_insert_rows(
                    tenant_id,
                    chunks,
                )

                if document_rows:
                    cursor.executemany(self._INSERT_DOCUMENTS_SQL, document_rows)
                if parent_context_rows:
                    cursor.executemany(
                        self._INSERT_PARENT_CONTEXTS_SQL,
                        parent_context_rows,
                    )
                if chunk_rows:
                    cursor.executemany(self._INSERT_CHUNKS_SQL, chunk_rows)
                if search_field_rows:
                    cursor.executemany(
                        self._INSERT_CHUNK_SEARCH_FIELDS_SQL,
                        search_field_rows,
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
                self._connection_factory.release(connection)

        await asyncio.to_thread(_replace)

    async def find_vector_candidates(
        self,
        tenant_id: str,
        query_embedding: list[float],
        top_k: int,
        status: str,
        filters: dict[str, object] | None = None,
    ) -> list[RetrievalCandidate]:
        resolved_filters = filters or {}
        allowed_document_ids = resolved_filters.get("allowed_document_ids")
        if isinstance(allowed_document_ids, list) and not allowed_document_ids:
            return []

        def _find() -> list[RetrievalCandidate]:
            connection = self._connection_factory.acquire()
            cursor = connection.cursor()
            try:
                user_roles = self._user_roles(resolved_filters)
                sql = (
                    "SELECT AE_RAG_CHUNKS.DOCUMENT_ID, AE_RAG_CHUNKS.CHUNK_ID, AE_RAG_CHUNKS.CONTENT, AE_RAG_CHUNKS.PAGE_NO, "
                    "COSINE_SIMILARITY(AE_RAG_CHUNKS.EMBEDDING, TO_REAL_VECTOR(?)) AS SCORE, "
                    "AE_RAG_CHUNKS.TABLE_ID, AE_RAG_CHUNKS.CHUNK_KIND, AE_RAG_CHUNKS.CONTENT_FORMAT, AE_RAG_CHUNKS.METADATA_JSON, AE_RAG_CHUNKS.PARENT_CONTEXT_ID, AE_RAG_PARENT_CONTEXTS.CONTENT "
                    "FROM AE_RAG_CHUNKS JOIN AE_RAG_DOCUMENTS "
                    "ON AE_RAG_DOCUMENTS.TENANT_ID = AE_RAG_CHUNKS.TENANT_ID "
                    "AND AE_RAG_DOCUMENTS.DOCUMENT_ID = AE_RAG_CHUNKS.DOCUMENT_ID "
                    "AND AE_RAG_DOCUMENTS.STATUS = 'indexed' "
                    "LEFT JOIN AE_RAG_PARENT_CONTEXTS "
                    "ON AE_RAG_PARENT_CONTEXTS.TENANT_ID = AE_RAG_CHUNKS.TENANT_ID "
                    "AND AE_RAG_PARENT_CONTEXTS.DOCUMENT_ID = AE_RAG_CHUNKS.DOCUMENT_ID "
                    "AND AE_RAG_PARENT_CONTEXTS.PARENT_CONTEXT_ID = AE_RAG_CHUNKS.PARENT_CONTEXT_ID "
                    "WHERE AE_RAG_CHUNKS.TENANT_ID = ? AND AE_RAG_CHUNKS.STATUS = ?"
                )
                params: list[object] = [
                    _JSON_CODEC.dump_vector(query_embedding),
                    tenant_id,
                    status,
                ]

                if isinstance(allowed_document_ids, list) and allowed_document_ids:
                    placeholders = ", ".join("?" for _ in allowed_document_ids)
                    sql += f" AND AE_RAG_CHUNKS.DOCUMENT_ID IN ({placeholders})"
                    params.extend(allowed_document_ids)

                sql += " ORDER BY SCORE DESC LIMIT ?"
                params.append(top_k)

                cursor.execute(sql, tuple(params))
                rows = cursor.fetchall()
                candidates: list[RetrievalCandidate] = []
                for row in rows:
                    metadata = _JSON_CODEC.load_json_object(row[8])
                    if not self._metadata_allows_roles(metadata, user_roles):
                        continue
                    metadata["page_no"] = row[3]
                    metadata["table_id"] = row[5]
                    metadata["chunk_kind"] = row[6]
                    metadata["content_format"] = row[7]
                    parent_context_id = row[9] if len(row) > 9 else None
                    parent_context_content = row[10] if len(row) > 10 else None
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
                    candidates.append(
                        RetrievalCandidate(
                            document_id=row[0],
                            chunk_id=row[1],
                            content=row[2],
                            score=row[4],
                            route=RetrievalRoute.HYBRID_DOCUMENT,
                            metadata=metadata,
                        )
                    )
                    if len(candidates) >= top_k:
                        break
                return candidates
            finally:
                cursor.close()
                self._connection_factory.release(connection)

        return await asyncio.to_thread(_find)

    async def find_exact_rule_matches(
        self,
        tenant_id: str,
        probe_fields: dict[str, str],
        normalized_probe_fields: dict[str, str],
        rules: list[DuplicateMatchRule],
        max_results: int,
        filters: dict[str, object] | None = None,
    ) -> list[RetrievalCandidate]:
        del probe_fields
        resolved_filters = filters or {}
        allowed_document_ids = resolved_filters.get("allowed_document_ids")
        if isinstance(allowed_document_ids, list) and not allowed_document_ids:
            return []

        def _find() -> list[RetrievalCandidate]:
            connection = self._connection_factory.acquire()
            cursor = connection.cursor()
            try:
                return self._find_exact_rule_matches_v2(
                    cursor=cursor,
                    tenant_id=tenant_id,
                    normalized_probe_fields=normalized_probe_fields,
                    rules=rules,
                    max_results=max_results,
                    filters=resolved_filters,
                )
            finally:
                cursor.close()
                self._connection_factory.release(connection)

        return await asyncio.to_thread(_find)

    async def find_fuzzy_rule_matches(
        self,
        tenant_id: str,
        probe_fields: dict[str, str],
        rules: list[DuplicateMatchRule],
        expanded_terms: list[str],
        max_results: int,
        filters: dict[str, object] | None = None,
    ) -> list[RetrievalCandidate]:
        resolved_filters = filters or {}
        allowed_document_ids = resolved_filters.get("allowed_document_ids")
        if isinstance(allowed_document_ids, list) and not allowed_document_ids:
            return []

        def _find() -> list[RetrievalCandidate]:
            connection = self._connection_factory.acquire()
            cursor = connection.cursor()
            try:
                return self._find_fuzzy_rule_matches_v2(
                    cursor=cursor,
                    tenant_id=tenant_id,
                    probe_fields=probe_fields,
                    rules=rules,
                    expanded_terms=expanded_terms,
                    max_results=max_results,
                    filters=resolved_filters,
                )
            finally:
                cursor.close()
                self._connection_factory.release(connection)

        return await asyncio.to_thread(_find)

    def _find_exact_rule_matches_v2(
        self,
        *,
        cursor,
        tenant_id: str,
        normalized_probe_fields: dict[str, str],
        rules: list[DuplicateMatchRule],
        max_results: int,
        filters: dict[str, object],
    ) -> list[RetrievalCandidate]:
        exact_rows: list[tuple[object, ...]] = []
        for rule in rules:
            if str(rule.match_type).strip().lower() != "exact":
                continue
            field_name = self._normalize_text(rule.field)
            probe_value = normalized_probe_fields.get(field_name, "").strip()
            if not field_name or not probe_value:
                continue
            exact_rows.append(
                (
                    field_name,
                    probe_value,
                    self._searchable_field_hash(probe_value),
                )
            )
        if not exact_rows:
            return []

        user_roles = self._user_roles(filters)
        value_cte_sql, value_cte_params = self._query_values_cte(
            exact_rows,
            ("FIELD_NAME", "PROBE_VALUE", "PROBE_HASH"),
        )
        allowed_document_ids = filters.get("allowed_document_ids")
        document_filter_sql, document_filter_params = self._match_query_document_filter(
            allowed_document_ids,
            column_name="SF.DOCUMENT_ID",
        )
        safe_limit = self._search_result_limit(
            max_results,
            match_count=len(exact_rows),
            user_roles=user_roles,
        )
        sql = (
            "WITH EXACT_RULES AS ("
            f"{value_cte_sql}"
            "), EXACT_MATCHES AS ("
            "SELECT SF.DOCUMENT_ID, SF.CHUNK_ID, ER.FIELD_NAME "
            "FROM AE_RAG_CHUNK_SEARCH_FIELDS SF "
            "JOIN EXACT_RULES ER ON ER.FIELD_NAME = SF.FIELD_NAME "
            "AND ER.PROBE_HASH = SF.NORMALIZED_FIELD_HASH "
            "AND ER.PROBE_VALUE = SF.NORMALIZED_FIELD_VALUE "
            "WHERE SF.TENANT_ID = ?"
            f"{document_filter_sql}"
            "), LIMITED_MATCHES AS ("
            "SELECT DOCUMENT_ID, CHUNK_ID, FIELD_NAME FROM EXACT_MATCHES "
            "ORDER BY DOCUMENT_ID, CHUNK_ID, FIELD_NAME LIMIT ?"
            ") "
            "SELECT LM.FIELD_NAME, C.DOCUMENT_ID, C.CHUNK_ID, C.CONTENT, C.PAGE_NO, C.TABLE_ID, C.CHUNK_KIND, "
            "C.CONTENT_FORMAT, C.METADATA_JSON, C.PARENT_CONTEXT_ID, PC.CONTENT "
            "FROM LIMITED_MATCHES LM "
            "JOIN AE_RAG_CHUNKS C "
            "ON C.TENANT_ID = ? AND C.DOCUMENT_ID = LM.DOCUMENT_ID AND C.CHUNK_ID = LM.CHUNK_ID "
            "JOIN AE_RAG_DOCUMENTS D "
            "ON D.TENANT_ID = C.TENANT_ID AND D.DOCUMENT_ID = C.DOCUMENT_ID AND D.STATUS = 'indexed' "
            "LEFT JOIN AE_RAG_PARENT_CONTEXTS PC "
            "ON PC.TENANT_ID = C.TENANT_ID AND PC.DOCUMENT_ID = C.DOCUMENT_ID AND PC.PARENT_CONTEXT_ID = C.PARENT_CONTEXT_ID "
            "WHERE C.STATUS = 'indexed' AND C.CHUNK_KIND = 'structured_row' "
            "ORDER BY C.DOCUMENT_ID, C.CHUNK_ID, LM.FIELD_NAME"
        )
        params = [
            *value_cte_params,
            tenant_id,
            *document_filter_params,
            safe_limit,
            tenant_id,
        ]
        cursor.execute(sql, tuple(params))

        matched_by_chunk_id: dict[str, RetrievalCandidate] = {}
        for row in cursor.fetchall():
            candidate = self._candidate_from_row(row[1:], route=RetrievalRoute.STRUCTURED_TABLE)
            if not self._metadata_allows_roles(candidate.metadata, user_roles):
                continue
            field_name = str(row[0])
            existing = matched_by_chunk_id.get(candidate.chunk_id)
            exact_fields = list(existing.metadata.get("exact_matches", [])) if existing else []
            exact_fields.append(field_name)
            matched_by_chunk_id[candidate.chunk_id] = RetrievalCandidate(
                document_id=candidate.document_id,
                chunk_id=candidate.chunk_id,
                content=candidate.content,
                score=0.0,
                route=candidate.route,
                metadata={
                    **candidate.metadata,
                    "exact_matches": list(dict.fromkeys(exact_fields).keys()),
                    "fuzzy_matches": dict(existing.metadata.get("fuzzy_matches", {})) if existing else {},
                    "matched_terms": list(existing.metadata.get("matched_terms", [])) if existing else [],
                },
            )
            if len(matched_by_chunk_id) >= safe_limit:
                break
        return list(matched_by_chunk_id.values())

    def _find_fuzzy_rule_matches_v2(
        self,
        *,
        cursor,
        tenant_id: str,
        probe_fields: dict[str, str],
        rules: list[DuplicateMatchRule],
        expanded_terms: list[str],
        max_results: int,
        filters: dict[str, object],
    ) -> list[RetrievalCandidate]:
        user_roles = self._user_roles(filters)
        allowed_document_ids = filters.get("allowed_document_ids")
        matched_by_chunk_id: dict[str, RetrievalCandidate] = {}
        for rule in rules:
            if str(rule.match_type).strip().lower() != "fuzzy":
                continue
            field_name = self._normalize_text(rule.field)
            probe_value = self._normalize_text(probe_fields.get(field_name, ""))
            if not field_name or not probe_value:
                continue
            candidate_terms = [probe_value]
            candidate_terms.extend(
                self._normalize_text(term) for term in expanded_terms if str(term).strip()
            )
            deduped_terms = list(dict.fromkeys(term for term in candidate_terms if term))
            if not deduped_terms:
                continue
            threshold = float(rule.threshold if rule.threshold is not None else 0.8)
            union_sql_parts: list[str] = []
            params: list[object] = []
            document_filter_sql, document_filter_params = self._match_query_document_filter(
                allowed_document_ids,
                column_name="SF.DOCUMENT_ID",
            )
            for candidate_term in deduped_terms:
                union_sql_parts.append(
                    "SELECT SF.DOCUMENT_ID, SF.CHUNK_ID, ? AS FIELD_NAME, ? AS MATCHED_TERM, SCORE() AS FUZZY_MATCH_SCORE "
                    "FROM AE_RAG_CHUNK_SEARCH_FIELDS SF "
                    "WHERE SF.TENANT_ID = ? AND SF.FIELD_NAME = ? "
                    f"AND CONTAINS(SF.NORMALIZED_FIELD_VALUE, ?, FUZZY ({threshold}, 'searchMode=text'))"
                    f"{document_filter_sql}"
                )
                params.extend(
                    [
                        field_name,
                        candidate_term,
                        tenant_id,
                        field_name,
                        candidate_term,
                        *document_filter_params,
                    ]
                )
            safe_limit = self._search_result_limit(
                max_results,
                match_count=len(deduped_terms),
                user_roles=user_roles,
            )
            sql = (
                "WITH TERM_MATCHES AS ("
                + " UNION ALL ".join(union_sql_parts)
                + "), LIMITED_MATCHES AS ("
                "SELECT DOCUMENT_ID, CHUNK_ID, FIELD_NAME, MATCHED_TERM, FUZZY_MATCH_SCORE "
                "FROM TERM_MATCHES ORDER BY FUZZY_MATCH_SCORE DESC, DOCUMENT_ID, CHUNK_ID LIMIT ?"
                ") "
                "SELECT LM.FIELD_NAME, LM.MATCHED_TERM, LM.FUZZY_MATCH_SCORE, C.DOCUMENT_ID, C.CHUNK_ID, C.CONTENT, C.PAGE_NO, "
                "C.TABLE_ID, C.CHUNK_KIND, C.CONTENT_FORMAT, C.METADATA_JSON, C.PARENT_CONTEXT_ID, PC.CONTENT "
                "FROM LIMITED_MATCHES LM "
                "JOIN AE_RAG_CHUNKS C "
                "ON C.TENANT_ID = ? AND C.DOCUMENT_ID = LM.DOCUMENT_ID AND C.CHUNK_ID = LM.CHUNK_ID "
                "JOIN AE_RAG_DOCUMENTS D "
                "ON D.TENANT_ID = C.TENANT_ID AND D.DOCUMENT_ID = C.DOCUMENT_ID AND D.STATUS = 'indexed' "
                "LEFT JOIN AE_RAG_PARENT_CONTEXTS PC "
                "ON PC.TENANT_ID = C.TENANT_ID AND PC.DOCUMENT_ID = C.DOCUMENT_ID AND PC.PARENT_CONTEXT_ID = C.PARENT_CONTEXT_ID "
                "WHERE C.STATUS = 'indexed' AND C.CHUNK_KIND = 'structured_row' "
                "ORDER BY LM.FUZZY_MATCH_SCORE DESC, C.DOCUMENT_ID, C.CHUNK_ID"
            )
            cursor.execute(sql, tuple([*params, safe_limit, tenant_id]))
            for row in cursor.fetchall():
                candidate = self._candidate_from_row(
                    (
                        row[3],
                        row[4],
                        row[5],
                        row[6],
                        row[7],
                        row[8],
                        row[9],
                        row[10],
                        row[11],
                        row[12],
                    ),
                    route=RetrievalRoute.STRUCTURED_TABLE,
                )
                if not self._metadata_allows_roles(candidate.metadata, user_roles):
                    continue
                existing = matched_by_chunk_id.get(candidate.chunk_id)
                fuzzy_scores = dict(existing.metadata.get("fuzzy_matches", {})) if existing else {}
                fuzzy_scores[field_name] = max(
                    float(fuzzy_scores.get(field_name, 0.0)),
                    float(row[2] or 0.0),
                )
                matched_terms = list(existing.metadata.get("matched_terms", [])) if existing else []
                matched_terms.append(str(row[1]))
                matched_by_chunk_id[candidate.chunk_id] = RetrievalCandidate(
                    document_id=candidate.document_id,
                    chunk_id=candidate.chunk_id,
                    content=candidate.content,
                    score=0.0,
                    route=candidate.route,
                    metadata={
                        **candidate.metadata,
                        "exact_matches": list(existing.metadata.get("exact_matches", [])) if existing else [],
                        "fuzzy_matches": fuzzy_scores,
                        "matched_terms": list(dict.fromkeys(matched_terms).keys()),
                    },
                )
                if len(matched_by_chunk_id) >= safe_limit:
                    break
        return list(matched_by_chunk_id.values())

    async def upsert_file_sync_state(self, tenant_id: str, state: dict[str, object]) -> None:
        def _upsert() -> None:
            connection = self._connection_factory.acquire()
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "UPSERT AE_RAG_FILE_SYNC_STATE (TENANT_ID, FILE_ID, SOURCE_UPDATED_AT, LAST_INDEXED_AT, PARSE_STATUS, CONTENT_HASH, METADATA_JSON) VALUES (?, ?, ?, ?, ?, ?, ?) WITH PRIMARY KEY",
                    (
                        tenant_id,
                        state.get("file_id", ""),
                        state.get("source_updated_at"),
                        state.get("last_indexed_at"),
                        state.get("parse_status", ""),
                        state.get("content_hash", ""),
                        _JSON_CODEC.dump_json(state.get("metadata", {})),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
                self._connection_factory.release(connection)

        await asyncio.to_thread(_upsert)
