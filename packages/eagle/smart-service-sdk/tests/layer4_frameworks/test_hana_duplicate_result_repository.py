import asyncio

from smart_service_sdk.layer1_domain.entities.duplicate_result import DuplicateResult
from smart_service_sdk.layer4_frameworks.repositories.hana_duplicate_result_repository import (
    HanaDuplicateResultRepository,
)


class _Cursor:
    def __init__(self) -> None:
        self.commands: list[tuple[str, object]] = []

    def execute(self, sql: str, params=None) -> None:
        self.commands.append((sql, params))

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


def test_hana_duplicate_result_repository_saves_result() -> None:
    cursor = _Cursor()
    repository = HanaDuplicateResultRepository(_Factory(_Connection(cursor)))

    saved = asyncio.run(
        repository.save_duplicate_result(
            DuplicateResult(
                id="result-1",
                record_id="REQ-1",
                tenant_id="tenant-1",
                score=0.82,
                candidates=[{"record_id": "CR-1"}],
                decision_trace=["matched"],
            )
        )
    )

    assert saved.id == "result-1"
    first_sql, params = cursor.commands[0]
    assert "INSERT INTO AE_RAG_CHECK_RESULTS" in first_sql
    assert params[0] == "result-1"
