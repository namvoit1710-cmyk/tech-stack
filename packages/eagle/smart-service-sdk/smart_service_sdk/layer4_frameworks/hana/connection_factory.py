from queue import Empty, Queue
from threading import Lock
from time import monotonic
from typing import Any

from smart_service_sdk.layer2_application.interfaces.logger_interface import (
    ILogger,
    NullLogger,
)
from smart_service_sdk.layer4_frameworks.hana.sql_identifiers import (
    quote_identifier,
    validate_schema_identifier,
)


class HanaConnectionFactory:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        schema: str,
        pool_size: int = 5,
        pool_timeout_seconds: float = 5.0,
        statement_timeout_ms: int | None = 30_000,
        logger: ILogger | None = None,
    ):
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._schema = validate_schema_identifier(schema) if schema else ""
        self._pool_size = pool_size
        self._pool_timeout_seconds = pool_timeout_seconds
        self._statement_timeout_ms = statement_timeout_ms
        self._pool: Queue[Any] = Queue(maxsize=pool_size)
        self._created_connections = 0
        self._connection_is_scoped: dict[int, bool] = {}
        self._lock = Lock()
        self._logger = logger or NullLogger()

    def acquire(self):
        return self._acquire(set_schema=True)

    def acquire_without_schema(self):
        return self._acquire(set_schema=False)

    def _acquire(self, set_schema: bool):
        deadline = monotonic() + self._pool_timeout_seconds
        while True:
            try:
                connection = self._pool.get_nowait()
            except Empty:
                with self._lock:
                    if self._created_connections < self._pool_size:
                        connection = self._create_connection(set_schema=set_schema)
                        self._created_connections += 1
                        self._connection_is_scoped[id(connection)] = set_schema
                        return connection
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for HANA connection")
                try:
                    connection = self._pool.get(timeout=remaining)
                except Empty as exc:
                    raise TimeoutError("Timed out waiting for HANA connection") from exc

            if self._connection_is_scoped.get(id(connection), True) != set_schema:
                self._discard_connection(connection)
                continue

            if not self._is_alive(connection):
                self._logger.warning("Discarding stale HANA connection from pool")
                self._discard_connection(connection)
                continue

            return connection

    def release(self, connection: Any) -> None:
        if not self._connection_is_scoped.get(id(connection), True):
            self._discard_connection(connection)
            return
        try:
            self._pool.put_nowait(connection)
        except Exception:
            self._logger.exception("Failed to return connection to pool, discarding")
            self._discard_connection(connection)

    def close(self) -> None:
        deadline = monotonic() + self._pool_timeout_seconds
        while monotonic() <= deadline:
            try:
                connection = self._pool.get_nowait()
            except Empty:
                break
            try:
                connection.close()
            except Exception:
                self._logger.exception("Error closing pooled connection")
            finally:
                self._connection_is_scoped.pop(id(connection), None)
                with self._lock:
                    if self._created_connections > 0:
                        self._created_connections -= 1

    def _create_connection(self, set_schema: bool = True):
        from hdbcli import dbapi

        connection = dbapi.connect(
            address=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            autocommit=False,
        )
        try:
            self._configure_connection(connection, set_schema=set_schema)
        except Exception as exc:
            self._logger.info("Error configuring new HANA connection", exc_info=True, exception=str(exc))
            try:
                connection.close()
            except Exception:
                self._logger.debug("Error closing failed new connection", exc_info=True)
            raise
        return connection

    def _configure_connection(self, connection: Any, *, set_schema: bool) -> None:
        cursor = connection.cursor()
        try:
            self._set_statement_timeout(cursor)
            if set_schema and self._schema:
                cursor.execute(f"SET SCHEMA {quote_identifier(self._schema)}")
        finally:
            cursor.close()

    def _set_statement_timeout(self, cursor: Any) -> None:
        timeout_ms = self._statement_timeout_ms
        if timeout_ms is None or int(timeout_ms) <= 0:
            return
        try:
            cursor.execute(f"SET 'statement_timeout' = '{int(timeout_ms)}'")
        except Exception as exc:
            self._logger.warning(
                "Failed to configure HANA statement timeout",
                error_class=exc.__class__.__name__,
            )

    def _is_alive(self, connection: Any) -> bool:
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT 1 FROM DUMMY")
            finally:
                cursor.close()
            return True
        except Exception:
            return False

    def _discard_connection(self, connection: Any) -> None:
        try:
            connection.close()
        except Exception:
            self._logger.debug("Error closing discarded connection", exc_info=True)
        finally:
            self._connection_is_scoped.pop(id(connection), None)
            with self._lock:
                if self._created_connections > 0:
                    self._created_connections -= 1
