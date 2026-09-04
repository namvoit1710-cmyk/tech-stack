import asyncio
import json

import pytest

from smart_service_sdk.layer2_application.interfaces.graph_extractor_interface import (
    ExtractedGraphEntity,
    ExtractedGraphMention,
    ExtractedGraphRelation,
    GraphExtractionResult,
)
from smart_service_sdk.layer4_frameworks.repositories import hana_graph_repository as repository_module
from smart_service_sdk.layer4_frameworks.repositories.hana_graph_repository import (
    HanaGraphRepository,
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


@pytest.fixture(autouse=True)
def _run_to_thread_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(repository_module.asyncio, "to_thread", _fake_to_thread)


def test_replace_file_graphs_batches_deletes_and_inserts() -> None:
    cursor = _Cursor([[("entity-stale",)]])
    connection_factory = _ConnectionFactory(cursor)
    repository = HanaGraphRepository(connection_factory)

    asyncio.run(
        repository.replace_file_graphs(
            tenant_id="tenant-1",
            file_id="file-1",
            document_graphs=[
                (
                    "doc-1",
                    GraphExtractionResult(
                        entities=(
                            ExtractedGraphEntity(
                                entity_id="entity-1",
                                canonical_name="Acme",
                                entity_type="organization",
                                aliases=("ACME",),
                            ),
                        ),
                        relations=(
                            ExtractedGraphRelation(
                                source_entity_id="entity-1",
                                target_entity_id="entity-2",
                                relation_type="supplies",
                                confidence=0.75,
                                evidence_chunk_ids=("chunk-1",),
                            ),
                        ),
                        mentions=(
                            ExtractedGraphMention(
                                entity_id="entity-1",
                                chunk_id="chunk-1",
                                surface_text="Acme",
                                start_offset=0,
                                end_offset=4,
                            ),
                        ),
                    ),
                ),
                (
                    "doc-2",
                    GraphExtractionResult(
                        entities=(
                            ExtractedGraphEntity(
                                entity_id="entity-2",
                                canonical_name="Bolt",
                                entity_type="product",
                            ),
                        ),
                    ),
                ),
            ],
        )
    )

    assert len(cursor.executed) == 4
    assert "SELECT DISTINCT ENTITY_ID FROM AE_RAG_GRAPH_MENTIONS" in cursor.executed[0][0]
    assert "DELETE FROM AE_RAG_GRAPH_MENTIONS" in cursor.executed[1][0]
    assert "DELETE FROM AE_RAG_GRAPH_RELATIONS" in cursor.executed[2][0]
    assert "DELETE FROM AE_RAG_GRAPH_ENTITIES" in cursor.executed[3][0]

    assert [sql for sql, _ in cursor.executemany_calls] == [
        HanaGraphRepository._UPSERT_GRAPH_ENTITIES_SQL,
        HanaGraphRepository._INSERT_GRAPH_RELATIONS_SQL,
        HanaGraphRepository._INSERT_GRAPH_MENTIONS_SQL,
    ]

    entity_sql, entity_rows = cursor.executemany_calls[0]
    assert entity_sql == HanaGraphRepository._UPSERT_GRAPH_ENTITIES_SQL
    assert len(entity_rows) == 2
    assert entity_rows[0][:4] == ("entity-1", "tenant-1", "Acme", "organization")
    assert json.loads(str(entity_rows[0][4])) == {"aliases": ["ACME"]}
    assert entity_rows[1][:4] == ("entity-2", "tenant-1", "Bolt", "product")
    assert json.loads(str(entity_rows[1][4])) == {}

    relation_sql, relation_rows = cursor.executemany_calls[1]
    assert relation_sql == HanaGraphRepository._INSERT_GRAPH_RELATIONS_SQL
    assert len(relation_rows) == 1
    assert relation_rows[0][1:6] == (
        "tenant-1",
        "entity-1",
        "entity-2",
        "supplies",
        0.75,
    )
    assert json.loads(str(relation_rows[0][6])) == {
        "document_id": "doc-1",
        "source_file_id": "file-1",
        "evidence_chunk_ids": ["chunk-1"],
    }

    mention_sql, mention_rows = cursor.executemany_calls[2]
    assert mention_sql == HanaGraphRepository._INSERT_GRAPH_MENTIONS_SQL
    assert len(mention_rows) == 1
    assert mention_rows[0][1:7] == ("tenant-1", "entity-1", "chunk-1", "Acme", 0, 4)
    assert json.loads(str(mention_rows[0][7])) == {
        "document_id": "doc-1",
        "source_file_id": "file-1",
    }
    assert connection_factory._connection.commit_count == 1
    assert connection_factory._connection.rollback_count == 0


def test_replace_file_graphs_with_empty_inputs_skips_batch_inserts() -> None:
    cursor = _Cursor([[]])
    connection_factory = _ConnectionFactory(cursor)
    repository = HanaGraphRepository(connection_factory)

    asyncio.run(
        repository.replace_file_graphs(
            tenant_id="tenant-1",
            file_id="file-1",
            document_graphs=[],
        )
    )

    assert len(cursor.executed) == 3
    assert cursor.executemany_calls == []
    assert connection_factory._connection.commit_count == 1
    assert connection_factory._connection.rollback_count == 0


def test_replace_file_graphs_rolls_back_when_batched_insert_fails() -> None:
    cursor = _Cursor(
        [[]],
        fail_on_executemany_sql=HanaGraphRepository._INSERT_GRAPH_MENTIONS_SQL,
    )
    connection_factory = _ConnectionFactory(cursor)
    repository = HanaGraphRepository(connection_factory)

    try:
        asyncio.run(
            repository.replace_file_graphs(
                tenant_id="tenant-1",
                file_id="file-1",
                document_graphs=[
                    (
                        "doc-1",
                        GraphExtractionResult(
                            entities=(
                                ExtractedGraphEntity(
                                    entity_id="entity-1",
                                    canonical_name="Acme",
                                    entity_type="organization",
                                ),
                            ),
                            mentions=(
                                ExtractedGraphMention(
                                    entity_id="entity-1",
                                    chunk_id="chunk-1",
                                    surface_text="Acme",
                                    start_offset=0,
                                    end_offset=4,
                                ),
                            ),
                        ),
                    )
                ],
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "executemany failed"
    else:
        raise AssertionError("replace_file_graphs should propagate executemany errors")

    assert connection_factory._connection.commit_count == 0
    assert connection_factory._connection.rollback_count == 1
