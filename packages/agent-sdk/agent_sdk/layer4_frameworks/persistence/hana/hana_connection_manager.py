"""SAP HANA connection manager with thread-safe connection pooling.

Structurally implements the IDatabaseConnection protocol defined in
``app.layer3_adapters.infrastructure.persistence.hana.base_repository``.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from queue import Empty, Queue
from typing import Any

logger = logging.getLogger(__name__)

# Regex to find :p0, :p1, … named placeholders used by HANA repos
_NAMED_PARAM_RE = re.compile(r":p(\d+)")


class HanaConnectionManager:
    """Thread-safe HANA connection pool.

    Provides ``execute_query`` and ``execute_write`` so it satisfies the
    ``IDatabaseConnection`` protocol without an explicit ``implements`` clause.
    """

    def __init__(self, settings: Any) -> None:
        self._host: str = settings.HANA_HOST
        self._port: int = settings.HANA_PORT
        self._user: str = settings.HANA_USERNAME
        self._password: str = settings.HANA_PASSWORD
        self._schema: str = settings.HANA_SCHEMA
        self._encrypt: bool = str(settings.HANA_ENCRYPT).lower() == "true"
        self._ssl_cert: str = settings.HANA_SSL_CERT

        self._pool_size: int = settings.HANA_POOL_SIZE
        self._max_overflow: int = settings.HANA_MAX_OVERFLOW
        self._pool_recycle: int = settings.HANA_POOL_RECYCLE  # seconds

        self._pool: Queue = Queue(maxsize=self._pool_size + self._max_overflow)
        self._created: int = 0
        self._lock = threading.Lock()
        # Track when each connection was created: id(conn) -> timestamp
        self._birth: dict[int, float] = {}

    # ------------------------------------------------------------------
    # Pool helpers
    # ------------------------------------------------------------------

    def _create_connection(self):
        from hdbcli import dbapi

        connect_kwargs: dict[str, Any] = {
            "address": self._host,
            "port": self._port,
            "user": self._user,
            "password": self._password,
            "autocommit": False,
        }
        if self._encrypt:
            connect_kwargs["encrypt"] = True
            if self._ssl_cert:
                connect_kwargs["sslTrustStore"] = self._ssl_cert
        conn = dbapi.connect(**connect_kwargs)
        if self._schema:
            cursor = conn.cursor()
            cursor.execute(f'SET SCHEMA "{self._schema}"')
            cursor.close()
        self._birth[id(conn)] = time.monotonic()
        return conn

    def _is_stale(self, conn) -> bool:
        stale_sentinel = time.monotonic() - self._pool_recycle - 1
        birth = self._birth.get(id(conn), stale_sentinel)
        return (time.monotonic() - birth) > self._pool_recycle

    def _acquire(self):
        """Acquire a healthy connection from the pool."""
        max_attempts = (
            self._pool_size + 1
        )  # at most pool_size stale connections to drain
        for attempt in range(max_attempts):
            # Non-blocking: try to get from pool immediately
            try:
                conn = self._pool.get_nowait()
            except Empty:
                # Pool empty — check if we can create a new connection
                with self._lock:
                    if self._created < self._pool_size + self._max_overflow:
                        self._created += 1
                        can_create = True
                    else:
                        can_create = False

                if can_create:
                    try:
                        return self._create_connection()
                    except Exception:
                        with self._lock:
                            self._created = max(0, self._created - 1)
                        raise

                # Pool exhausted — block waiting for a connection to be returned
                try:
                    conn = self._pool.get(timeout=30)
                except Empty:
                    raise ConnectionError(
                        "Timed out waiting for a connection from the pool."
                    )

            if self._is_stale(conn):
                self._discard(conn)
                continue  # try next connection

            return conn

        raise ConnectionError(
            f"All {self._pool_size} connections in pool are stale. "
            "Cannot acquire a healthy connection."
        )

    def _release(self, conn) -> None:
        try:
            self._pool.put_nowait(conn)
        except Exception:
            self._discard(conn)

    def _discard(self, conn) -> None:
        self._birth.pop(id(conn), None)
        with self._lock:
            self._created = max(0, self._created - 1)
        try:
            conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Parameter translation  :p0, :p1 → positional ?
    # ------------------------------------------------------------------

    @staticmethod
    def _bind_params(sql: str, params: tuple) -> tuple[str, tuple]:
        """Translate ``:p0``, ``:p1`` named placeholders to ``?``.

        Returns ``(new_sql, ordered_params)`` ready for hdbcli cursor.
        """
        matches = list(_NAMED_PARAM_RE.finditer(sql))
        if not matches:
            return sql, params

        ordered: list[Any] = []
        for m in matches:
            idx = int(m.group(1))
            if idx < len(params):
                ordered.append(params[idx])
            else:
                ordered.append(None)

        new_sql = _NAMED_PARAM_RE.sub("?", sql)
        return new_sql, tuple(ordered)

    # ------------------------------------------------------------------
    # Public API (IDatabaseConnection protocol)
    # ------------------------------------------------------------------

    def execute_query(self, sql: str, params: tuple = ()) -> list[dict]:
        sql, params = self._bind_params(sql, params)
        conn = self._acquire()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            if cursor.description is None:
                rows = []
            else:
                columns = [desc[0].lower() for desc in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            cursor.close()
        except Exception:
            self._discard(conn)
            raise
        else:
            self._release(conn)
        return rows

    def execute_write(self, sql: str, params: tuple = ()) -> int:
        sql, params = self._bind_params(sql, params)
        conn = self._acquire()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rowcount = cursor.rowcount if cursor.rowcount is not None else 0
            conn.commit()
            cursor.close()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            self._discard(conn)
            raise
        else:
            self._release(conn)
        return rowcount

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Pre-populate the connection pool."""
        logger.info(
            "Initializing HANA connection pool (size=%d, overflow=%d, recycle=%ds)",
            self._pool_size,
            self._max_overflow,
            self._pool_recycle,
        )
        for _ in range(self._pool_size):
            with self._lock:
                if self._created >= self._pool_size + self._max_overflow:
                    break
                self._created += 1
            try:
                conn = self._create_connection()
                self._pool.put_nowait(conn)
            except Exception as exc:
                with self._lock:
                    self._created = max(0, self._created - 1)
                logger.warning("Failed to pre-populate pool connection: %s", exc)

    def close(self) -> None:
        """Drain the pool and close all connections."""
        logger.info("Closing HANA connection pool...")
        closed = 0
        while True:
            try:
                conn = self._pool.get_nowait()
                self._birth.pop(id(conn), None)
                try:
                    conn.close()
                except Exception:
                    pass
                closed += 1
            except Empty:
                break
        with self._lock:
            self._created = 0
        logger.info("Closed %d pooled connections.", closed)
