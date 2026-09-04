import asyncio

from smart_service_sdk.layer1_domain.entities.duplicate_match_rule import DuplicateMatchRule
from smart_service_sdk.layer4_frameworks.repositories.hana_chunk_repository import (
    HanaChunkRepository,
)


class _Cursor:
    def __init__(
        self,
        rows_by_call: list[list[tuple[object, ...]]],
        *,
        fail_on_executemany_sql: str | None = None,
    ) -> None:
        self._rows_by_call = list(rows_by_call)
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []
        self.fail_on_executemany_sql = fail_on_executemany_sql

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        self.executed.append((sql, params))

    def executemany(self, sql: str, params: list[tuple[object, ...]]) -> None:
        self.executemany_calls.append((sql, list(params)))
        if self.fail_on_executemany_sql == sql:
            raise RuntimeError("executemany failed")

    def fetchall(self) -> list[tuple[object, ...]]:
        if not self._rows_by_call:
            return []
        return self._rows_by_call.pop(0)

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self) -> _Cursor:
        return self._cursor

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class _ConnectionFactory:
    def __init__(self, cursor: _Cursor) -> None:
        self._connection = _Connection(cursor)

    def acquire(self) -> _Connection:
        return self._connection

    def release(self, connection) -> None:
        del connection


def test_exact_rule_matches_batch_against_search_field_table() -> None:
    cursor = _Cursor(
        [
            [
                (
                    "material name",
                    "DOC-1",
                    "CHUNK-1",
                    "row-window-content",
                    1,
                    "table-1",
                    "structured_row",
                    "text",
                    "{}",
                    None,
                    None,
                ),
                (
                    "material type",
                    "DOC-1",
                    "CHUNK-1",
                    "row-window-content",
                    1,
                    "table-1",
                    "structured_row",
                    "text",
                    "{}",
                    None,
                    None,
                ),
            ],
        ]
    )
    repository = HanaChunkRepository(_ConnectionFactory(cursor))

    matches = asyncio.run(
        repository.find_exact_rule_matches(
            tenant_id="tenant-1",
            probe_fields={
                "material name": "Finished Product: Wireless Earbuds",
                "material type": "Electronics",
            },
            normalized_probe_fields={
                "material name": "finished product: wireless earbuds",
                "material type": "electronics",
            },
            rules=[
                DuplicateMatchRule(field="material name", match_type="exact"),
                DuplicateMatchRule(field="material type", match_type="exact"),
            ],
            max_results=5,
        )
    )

    assert len(cursor.executed) == 1
    sql, params = cursor.executed[0]
    assert "EXACT_RULES AS" in sql
    assert "AE_RAG_CHUNK_SEARCH_FIELDS" in sql
    assert "NORMALIZED_FIELD_HASH" in sql
    assert "LIMITED_MATCHES" in sql
    assert params.count("tenant-1") == 2
    assert len(matches) == 1
    assert matches[0].metadata["exact_matches"] == ["material name", "material type"]


def test_fuzzy_rule_matches_batch_terms_per_field_against_search_field_table() -> None:
    cursor = _Cursor(
        [
            [
                (
                    "material description",
                    "wireless earbud",
                    0.72,
                    "DOC-1",
                    "CHUNK-1",
                    "row-window-content",
                    1,
                    "table-1",
                    "structured_row",
                    "text",
                    "{}",
                    None,
                    None,
                ),
                (
                    "material description",
                    "wireless audio",
                    0.91,
                    "DOC-1",
                    "CHUNK-1",
                    "row-window-content",
                    1,
                    "table-1",
                    "structured_row",
                    "text",
                    "{}",
                    None,
                    None,
                ),
            ],
        ]
    )
    repository = HanaChunkRepository(_ConnectionFactory(cursor))

    matches = asyncio.run(
        repository.find_fuzzy_rule_matches(
            tenant_id="tenant-1",
            probe_fields={"material description": "Wireless Earbud"},
            rules=[
                DuplicateMatchRule(
                    field="material description",
                    match_type="fuzzy",
                    threshold=0.7,
                )
            ],
            expanded_terms=["wireless audio"],
            max_results=5,
        )
    )

    assert len(cursor.executed) == 1
    sql, params = cursor.executed[0]
    assert "TERM_MATCHES AS" in sql
    assert sql.count("CONTAINS(SF.NORMALIZED_FIELD_VALUE") == 2
    assert "LIMITED_MATCHES" in sql
    assert "CONTENT_JSON" not in sql
    assert params[0] == "material description"
    assert params[1] == "wireless earbud"
    assert len(matches) == 1
    assert matches[0].metadata["fuzzy_matches"]["material description"] == 0.91
    assert matches[0].metadata["matched_terms"] == [
        "wireless earbud",
        "wireless audio",
    ]


def test_replace_file_chunks_batches_deletes_and_inserts() -> None:
    cursor = _Cursor([])
    connection_factory = _ConnectionFactory(cursor)
    repository = HanaChunkRepository(connection_factory)

    asyncio.run(
        repository.replace_file_chunks(
            tenant_id="tenant-1",
            file_id="file-1",
            documents=[
                {
                    "document_id": "doc-1",
                    "source_file_id": "file-1",
                    "content_hash": "hash-1",
                    "status": "indexed",
                    "doc_type": "structured_row",
                    "mime_type": "text/csv",
                    "filename": "source.csv",
                    "metadata": {"row_number": 1},
                }
            ],
            chunks=[
                {
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "source_file_id": "file-1",
                    "content_hash": "hash-1",
                    "status": "indexed",
                    "doc_type": "structured_row",
                    "mime_type": "text/csv",
                    "row_number": 1,
                    "chunk_kind": "semantic_text",
                    "content_format": "text",
                    "content": "Nickname: Acme",
                    "metadata": {"row_number": 1, "parent_context_id": "parent-1"},
                    "embedding": [1.0, 2.0],
                    "searchable_fields": [
                        {
                            "field_name": "nickname",
                            "field_value": "Acme",
                            "normalized_field_value": "acme",
                            "row_number": 1,
                        }
                    ],
                }
            ],
        )
    )

    assert [sql for sql, _ in cursor.executed] == [
        HanaChunkRepository._DELETE_PARENT_CONTEXTS_SQL,
        HanaChunkRepository._DELETE_CHUNK_SEARCH_FIELDS_SQL,
        HanaChunkRepository._DELETE_CHUNKS_SQL,
        HanaChunkRepository._DELETE_DOCUMENTS_SQL,
    ]
    assert [sql for sql, _ in cursor.executemany_calls] == [
        HanaChunkRepository._INSERT_DOCUMENTS_SQL,
        HanaChunkRepository._INSERT_PARENT_CONTEXTS_SQL,
        HanaChunkRepository._INSERT_CHUNKS_SQL,
        HanaChunkRepository._INSERT_CHUNK_SEARCH_FIELDS_SQL,
    ]
    document_sql, document_rows = cursor.executemany_calls[0]
    assert document_sql == HanaChunkRepository._INSERT_DOCUMENTS_SQL
    assert len(document_rows) == 1
    assert document_rows[0][:4] == ("doc-1", "tenant-1", "file-1", "hash-1")

    parent_context_sql, parent_context_rows = cursor.executemany_calls[1]
    assert parent_context_sql == HanaChunkRepository._INSERT_PARENT_CONTEXTS_SQL
    assert len(parent_context_rows) == 1
    assert parent_context_rows[0][:4] == (
        "parent-1",
        "tenant-1",
        "doc-1",
        "semantic_text",
    )

    chunk_sql, chunk_rows = cursor.executemany_calls[2]
    assert chunk_sql == HanaChunkRepository._INSERT_CHUNKS_SQL
    assert len(chunk_rows) == 1
    assert chunk_rows[0][0:4] == ("chunk-1", "tenant-1", "doc-1", "file-1")
    assert "TO_REAL_VECTOR(?)" in chunk_sql

    search_sql, search_rows = cursor.executemany_calls[3]
    assert search_sql == HanaChunkRepository._INSERT_CHUNK_SEARCH_FIELDS_SQL
    assert search_rows == [
        ("tenant-1", "doc-1", "chunk-1", 1, "nickname", "Acme", "acme", search_rows[0][7])
    ]
    assert len(search_rows[0][7]) == 64
    assert connection_factory._connection.commit_count == 1
    assert connection_factory._connection.rollback_count == 0


def test_replace_file_chunks_with_empty_inputs_skips_batch_inserts() -> None:
    cursor = _Cursor([])
    connection_factory = _ConnectionFactory(cursor)
    repository = HanaChunkRepository(connection_factory)

    asyncio.run(
        repository.replace_file_chunks(
            tenant_id="tenant-1",
            file_id="file-1",
            documents=[],
            chunks=[],
        )
    )

    assert len(cursor.executed) == 4
    assert cursor.executemany_calls == []
    assert connection_factory._connection.commit_count == 1
    assert connection_factory._connection.rollback_count == 0


def test_replace_file_chunks_rolls_back_when_batched_insert_fails() -> None:
    cursor = _Cursor(
        [],
        fail_on_executemany_sql=HanaChunkRepository._INSERT_CHUNKS_SQL,
    )
    connection_factory = _ConnectionFactory(cursor)
    repository = HanaChunkRepository(connection_factory)

    try:
        asyncio.run(
            repository.replace_file_chunks(
                tenant_id="tenant-1",
                file_id="file-1",
                documents=[
                    {
                        "document_id": "doc-1",
                        "metadata": {},
                    }
                ],
                chunks=[
                    {
                        "chunk_id": "chunk-1",
                        "document_id": "doc-1",
                        "content_hash": "hash-1",
                        "content": "content",
                        "metadata": {},
                        "embedding": [1.0],
                    }
                ],
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "executemany failed"
    else:
        raise AssertionError("replace_file_chunks should propagate executemany errors")

    assert connection_factory._connection.commit_count == 0
    assert connection_factory._connection.rollback_count == 1
