import asyncio

from smart_service_sdk.layer4_frameworks.repositories.hana_runtime_setting_repository import (
    HanaRuntimeSettingRepository,
)


class _Cursor:
    def __init__(self, fetchall_results=None) -> None:
        self.commands: list[tuple[str, object]] = []
        self._fetchall_results = list(fetchall_results or [])

    def execute(self, sql: str, params=None) -> None:
        self.commands.append((sql, params))

    def fetchall(self):
        if self._fetchall_results:
            return self._fetchall_results.pop(0)
        return []

    def close(self) -> None:
        self.commands.append(("close", None))


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _Cursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class _Factory:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.released: list[_Connection] = []

    def acquire(self) -> _Connection:
        return self.connection

    def release(self, connection: _Connection) -> None:
        self.released.append(connection)


def test_hana_runtime_setting_repository_reads_many_keys() -> None:
    cursor = _Cursor(
        fetchall_results=[
            [
                ("SEARCH_FUZZY_THRESHOLD", "0.8"),
                ("SEARCH_MAX_RESULTS", "10"),
            ]
        ]
    )
    repository = HanaRuntimeSettingRepository(_Factory(_Connection(cursor)))

    values = asyncio.run(
        repository.get_many(["SEARCH_FUZZY_THRESHOLD", "SEARCH_MAX_RESULTS"])
    )

    assert values == {
        "SEARCH_FUZZY_THRESHOLD": "0.8",
        "SEARCH_MAX_RESULTS": "10",
    }
    assert 'SELECT "KEY", "VALUE" FROM AE_SERVICE_CONFIGURATIONS' in cursor.commands[0][0]


def test_hana_runtime_setting_repository_upserts_value() -> None:
    cursor = _Cursor()
    connection = _Connection(cursor)
    repository = HanaRuntimeSettingRepository(_Factory(connection))

    asyncio.run(repository.set("SEARCH_MAX_RESULTS", "5"))

    assert (
        'UPSERT AE_SERVICE_CONFIGURATIONS ("KEY", "VALUE") VALUES (?, ?) WITH PRIMARY KEY'
        in cursor.commands[0][0]
    )
    assert cursor.commands[0][1] == ("SEARCH_MAX_RESULTS", "5")
    assert connection.committed is True
