"""SAP HANA connection manager with thread-safe connection pooling.

Structurally implements the IDatabaseConnection protocol so that HANA
repositories can delegate ``execute_query`` / ``execute_write`` directly.

The sync methods (``execute_query``, ``execute_write``,
``execute_write_returning_rowcount``) block the calling thread for the
duration of the SQL roundtrip. When called from an async coroutine
they also block the event loop.

The async wrappers (``aexecute_query``, ``aexecute_write``,
``aexecute_write_returning_rowcount``) offload the same call into a
dedicated ``ThreadPoolExecutor`` via ``run_in_executor`` so the event
loop stays unblocked. The executor is installed by ``main.py`` lifespan
under ``ASYNC_HANA_ENABLED`` and registered via ``set_executor``. When
no executor is installed the async wrappers fall back to the calling
loop's default executor; behavior is still correct, just less throughput.

The two paths share the same connection pool — a single per-call
``_acquire`` / ``_release`` cycle either runs in the caller's thread
(sync) or in the executor's worker thread (async). Repos can mix.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Queue
from typing import Any, Optional

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
        self._schema: str = getattr(settings, 'HANA_SCHEMA', '') or settings.HANA_USERNAME
        print(self._schema)
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
        # Dedicated executor for ``aexecute_*`` async wrappers. Installed
        # by ``main.py`` lifespan when ``ASYNC_HANA_ENABLED`` is true.
        # ``None`` means async wrappers fall back to the running loop's
        # default executor (still correct, just untuned).
        self._executor: Optional[ThreadPoolExecutor] = None
        # Optional monitor for pool-level observability. Set via
        # ``set_monitor`` from bootstrap. When None, all emissions are
        # no-ops — this manager runs the same in tests / mock mode.
        self._monitor: Any = None

    # ------------------------------------------------------------------
    # Pool helpers
    # ------------------------------------------------------------------

    def _create_connection(self) -> Any:
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

    def _is_stale(self, conn: Any) -> bool:
        birth = self._birth.get(id(conn), 0)
        return (time.monotonic() - birth) > self._pool_recycle

    def _acquire(self) -> Any:
        # Try to get a connection from the pool (non-blocking first)
        try:
            conn = self._pool.get_nowait()
            if self._is_stale(conn):
                self._discard(conn)
                return self._acquire()
            self._emit_acquire_wait(0.0)
            return conn
        except Empty:
            pass

        # Check if we can create a new connection
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

        # Pool exhausted — block until one is returned (lock NOT held).
        # Emit a histogram observation of how long the wait took so
        # ops can spot under-sized pools (``hana_pool_acquire_wait_ms``)
        # and a counter on the 30-second timeout
        # (``hana_pool_acquire_timeout_total``).
        wait_start = time.monotonic()
        try:
            conn = self._pool.get(timeout=30)
        except Empty:
            self._emit_acquire_timeout()
            raise
        finally:
            self._emit_acquire_wait(time.monotonic() - wait_start)
        if self._is_stale(conn):
            self._discard(conn)
            return self._acquire()
        return conn

    def _release(self, conn: Any) -> None:
        try:
            self._pool.put_nowait(conn)
        except Exception:
            self._discard(conn)

    def _discard(self, conn: Any) -> None:
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
        except Exception as exc:
            logger.error(exc)
            self._discard(conn)
            raise
        else:
            self._release(conn)
        return rows

    def execute_write(self, sql: str, params: tuple = ()) -> None:
        sql, params = self._bind_params(sql, params)
        conn = self._acquire()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params)
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

    def execute_write_returning_rowcount(self, sql: str, params: tuple = ()) -> int:
        """Run a write and return the affected row count.

        Phase M.2.1 ``hana_run_repo.save`` needs this for the CAS UPDATE
        (``... WHERE ID = ? AND VERSION = ?``): rowcount 0 signals a
        version mismatch. Regular ``execute_write`` doesn't expose it.
        """
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
    # Async wrappers — Part 2 of the async-I/O fix.
    #
    # Each ``aexecute_*`` delegates to its sync counterpart via
    # ``loop.run_in_executor`` against the registered executor (or the
    # loop's default if none registered). Caller awaits without blocking
    # the event loop.
    # ------------------------------------------------------------------

    def set_executor(self, executor: Optional[ThreadPoolExecutor]) -> None:
        """Install (or clear) the dedicated executor used by ``aexecute_*``.

        Called once during ``main.py`` lifespan startup when
        ``ASYNC_HANA_ENABLED`` is true. ``executor=None`` reverts async
        calls to the loop's default executor (Python's
        ``min(32, cpu+4)`` pool), which works for tests but is unsuited
        for prod load.
        """
        self._executor = executor

    async def aexecute_query(
        self, sql: str, params: tuple = (),
    ) -> list[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, self.execute_query, sql, params,
        )

    async def aexecute_write(
        self, sql: str, params: tuple = (),
    ) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            self._executor, self.execute_write, sql, params,
        )

    async def aexecute_write_returning_rowcount(
        self, sql: str, params: tuple = (),
    ) -> int:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self.execute_write_returning_rowcount,
            sql, params,
        )

    async def arun(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Run an arbitrary sync function in the dedicated executor.

        Lets repos expose async variants of multi-statement methods
        (e.g. the CAS ``save``: SELECT VERSION → INSERT/UPDATE WHERE
        VERSION = expected) without rewriting the sync logic. The
        whole sync method runs on one executor thread; the awaiter
        sees one future.

        Falls back to the loop's default executor when ``set_executor``
        hasn't been called.
        """
        import functools
        loop = asyncio.get_running_loop()
        self._sample_executor_depth()
        return await loop.run_in_executor(
            self._executor, functools.partial(fn, *args, **kwargs),
        )

    # ------------------------------------------------------------------
    # Observability hooks — Part 2 of the async-I/O fix.
    #
    # Bootstrap calls ``set_monitor`` once. Until then, emissions are
    # no-ops (the ``getattr(self._monitor, ...)`` guards keep this
    # manager usable in tests that don't wire a monitor).
    # ------------------------------------------------------------------

    def set_monitor(self, monitor: Any) -> None:
        """Install the ``IMonitor`` used for pool + executor metrics."""
        self._monitor = monitor

    def _emit_acquire_wait(self, seconds: float) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.histogram(
                "hana_pool_acquire_wait_seconds", seconds,
            )
        except Exception:
            # Metrics must never break request flow.
            pass

    def _emit_acquire_timeout(self) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.increment("hana_pool_acquire_timeout_total")
        except Exception:
            pass

    def _sample_executor_depth(self) -> None:
        """Best-effort sample of the dedicated executor's queued work.

        ``ThreadPoolExecutor`` exposes ``_work_queue.qsize()`` which is
        the count of submitted-but-not-yet-running tasks. Sampling on
        every ``arun`` call gives a high-frequency gauge without needing
        a background sampling thread.
        """
        if self._monitor is None or self._executor is None:
            return
        try:
            depth = self._executor._work_queue.qsize()  # type: ignore[attr-defined]
            self._monitor.track("hana_executor_queue_depth", float(depth))
        except Exception:
            pass

    def execute_procedure(self, proc_name: str, params: tuple = ()) -> list[dict]:
        """Execute a stored procedure and return results from cursor.
        
        Handles procedures that return results via cursor (no OUT parameters).
        
        Args:
            proc_name: The name of the stored procedure (e.g., 'GET_ALL_CHILD_WORKFLOWS_IN_WORKFLOW')
            params: Tuple of input parameters in order
            
        Returns:
            List of dictionaries representing the result rows
        """
        conn = self._acquire()
        try:
            cursor = conn.cursor()
            # Build the CALL statement with positional ? placeholders
            placeholders = ", ".join("?" * len(params))
            sql = f"CALL {proc_name}({placeholders})"
            cursor.execute(sql, params)
            
            # Fetch results from cursor
            rows = []
            if cursor.description:
                columns = [desc[0].lower() for desc in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            cursor.close()
        except Exception:
            self._discard(conn)
            raise
        else:
            self._release(conn)
        return rows

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
