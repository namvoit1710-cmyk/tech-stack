import asyncio
from datetime import UTC, datetime

from smart_service_sdk.layer1_domain.entities.background_job_record import (
    BackgroundJobRecord,
)
from smart_service_sdk.layer4_frameworks.repositories.hana_background_job_repository import (
    HanaBackgroundJobRepository,
)


class _Cursor:
    def __init__(self, fetchone_result=None, fetchall_result=None) -> None:
        self.commands: list[tuple[str, object]] = []
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result or []

    def execute(self, sql: str, params=None) -> None:
        self.commands.append((sql, params))

    def fetchone(self):
        return self._fetchone_result

    def fetchall(self):
        return list(self._fetchall_result)

    def close(self) -> None:
        self.commands.append(("close", None))


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.committed = False

    def cursor(self) -> _Cursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.committed = False


class _Factory:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.released: list[_Connection] = []

    def acquire(self) -> _Connection:
        return self.connection

    def release(self, connection: _Connection) -> None:
        self.released.append(connection)


def test_hana_background_job_repository_save_and_get() -> None:
    created_at = datetime(2026, 6, 22, tzinfo=UTC)
    cursor = _Cursor(
        fetchone_result=(
            "job-1",
            "tenant-1",
            "accepted",
            created_at,
            created_at,
            None,
            None,
            "",
            '{"job_type":"duplicate_index_import","progress":0.0,"file_ids":["file-a"]}',
        )
    )
    repository = HanaBackgroundJobRepository(_Factory(_Connection(cursor)))

    saved = asyncio.run(
        repository.save(
            BackgroundJobRecord(
                id="job-1",
                tenant_id="tenant-1",
                job_type="duplicate_index_import",
                status="accepted",
                detail={"file_ids": ["file-a"]},
                created_at=created_at,
            )
        )
    )
    loaded = asyncio.run(repository.get("tenant-1", "job-1"))

    assert saved.id == "job-1"
    assert loaded is not None
    assert loaded.job_type == "duplicate_index_import"
    assert loaded.detail["file_ids"] == ["file-a"]
    first_sql, _params = cursor.commands[0]
    assert "UPSERT AE_RAG_BACKGROUND_JOBS" in first_sql
    assert "JOB_ID" in first_sql
    assert "CREATED_AT" in first_sql
    assert "UPDATED_AT" in first_sql


def test_hana_background_job_repository_list_by_status() -> None:
    created_at = datetime(2026, 6, 24, tzinfo=UTC)
    cursor = _Cursor(
        fetchall_result=[
            (
                "job-1",
                "tenant-1",
                "running",
                created_at,
                created_at,
                created_at,
                None,
                "",
                '{"job_type":"duplicate_index_import","progress":0.0,"file_ids":["file-a"],"file_results":[{"file_id":"file-a","status":"running"}]}',
            )
        ]
    )
    repository = HanaBackgroundJobRepository(_Factory(_Connection(cursor)))

    loaded = asyncio.run(repository.list_by_status("running"))

    assert len(loaded) == 1
    assert loaded[0].id == "job-1"
    assert loaded[0].tenant_id == "tenant-1"
    assert loaded[0].status == "running"
    assert loaded[0].detail["file_ids"] == ["file-a"]
    sql, params = cursor.commands[0]
    assert "FROM AE_RAG_BACKGROUND_JOBS WHERE STATUS = ?" in sql
    assert params == ("running",)
