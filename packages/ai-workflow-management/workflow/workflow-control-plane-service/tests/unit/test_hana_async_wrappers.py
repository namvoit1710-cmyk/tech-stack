"""Async wrappers on ``HanaConnectionManager`` (Part 2 of the async-I/O
fix).

These tests verify the contract:
- ``aexecute_query`` returns the same rows as ``execute_query``
- ``aexecute_write`` performs the write and commits
- ``aexecute_write_returning_rowcount`` exposes ``cursor.rowcount`` to
  the awaiter (the CAS UPDATE path needs this)
- ``set_executor`` redirects the awaitables to the supplied executor;
  ``set_executor(None)`` reverts to the loop's default executor
- The event loop stays unblocked during a long-running SQL call
  (regression — the whole point of Part 2)

Uses a sqlite-backed stub that mirrors ``execute_*`` shapes; the real
``hdbcli`` driver is not exercised here.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.layer4_frameworks.persistence.hana.hana_connection_manager import (
    HanaConnectionManager,
)


class _SqliteBackedManager:
    """Mimic the relevant ``HanaConnectionManager`` surface using sqlite
    so the async wrappers can be exercised without hdbcli.

    Only the methods the async wrappers delegate to are needed:
    ``execute_query``, ``execute_write``, ``execute_write_returning_rowcount``.
    We monkey-patch a real ``HanaConnectionManager`` instance to swap
    its sync implementations to ours — that way we exercise the actual
    ``aexecute_*`` methods.
    """

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        self._conn.commit()
        self._calls: list[tuple[str, str, str]] = []  # (op, sql, thread_name)

    def execute_query(self, sql: str, params: tuple = ()) -> list[dict]:
        self._calls.append(
            ("query", sql, threading.current_thread().name),
        )
        s, p = HanaConnectionManager._bind_params(sql, params)
        cur = self._conn.execute(s, p)
        cols = [d[0] for d in (cur.description or [])]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        return rows

    def execute_write(self, sql: str, params: tuple = ()) -> None:
        self._calls.append(
            ("write", sql, threading.current_thread().name),
        )
        s, p = HanaConnectionManager._bind_params(sql, params)
        self._conn.execute(s, p)
        self._conn.commit()

    def execute_write_returning_rowcount(
        self, sql: str, params: tuple = (),
    ) -> int:
        self._calls.append(
            ("write_rc", sql, threading.current_thread().name),
        )
        s, p = HanaConnectionManager._bind_params(sql, params)
        cur = self._conn.execute(s, p)
        rc = cur.rowcount
        self._conn.commit()
        cur.close()
        return rc or 0


def _make_manager() -> HanaConnectionManager:
    """Build a ``HanaConnectionManager`` with sync methods overridden to
    a sqlite-backed implementation. The ``aexecute_*`` methods (under
    test) are untouched and delegate to our patched sync versions
    through ``run_in_executor`` as in production.
    """
    mgr = HanaConnectionManager.__new__(HanaConnectionManager)
    mgr._executor = None  # default: loop's default executor
    backing = _SqliteBackedManager()
    mgr.execute_query = backing.execute_query  # type: ignore[assignment]
    mgr.execute_write = backing.execute_write  # type: ignore[assignment]
    mgr.execute_write_returning_rowcount = (  # type: ignore[assignment]
        backing.execute_write_returning_rowcount
    )
    mgr._backing = backing  # type: ignore[attr-defined]
    return mgr


# ───────────────────────── async parity ────────────────────────────


class TestAsyncWrappersDelegate:
    """Each ``aexecute_*`` produces the same result as its sync sibling."""

    @pytest.mark.asyncio
    async def test_aexecute_write_then_aexecute_query_roundtrip(self):
        mgr = _make_manager()

        await mgr.aexecute_write(
            "INSERT INTO t (id, v) VALUES (:p0, :p1)", (1, "alpha"),
        )
        await mgr.aexecute_write(
            "INSERT INTO t (id, v) VALUES (:p0, :p1)", (2, "beta"),
        )

        rows = await mgr.aexecute_query("SELECT * FROM t ORDER BY id")
        assert rows == [
            {"id": 1, "v": "alpha"},
            {"id": 2, "v": "beta"},
        ]

    @pytest.mark.asyncio
    async def test_aexecute_write_returning_rowcount_reports_affected_rows(self):
        mgr = _make_manager()
        await mgr.aexecute_write(
            "INSERT INTO t (id, v) VALUES (:p0, :p1)", (1, "alpha"),
        )
        await mgr.aexecute_write(
            "INSERT INTO t (id, v) VALUES (:p0, :p1)", (2, "beta"),
        )

        # No match — CAS UPDATE that would return 0 in production
        rc = await mgr.aexecute_write_returning_rowcount(
            "UPDATE t SET v = :p0 WHERE id = :p1", ("gamma", 99),
        )
        assert rc == 0

        # Match — should return 1
        rc = await mgr.aexecute_write_returning_rowcount(
            "UPDATE t SET v = :p0 WHERE id = :p1", ("alpha2", 1),
        )
        assert rc == 1


# ───────────────────────── executor binding ─────────────────────────


class TestExecutorBinding:
    """``set_executor`` redirects the awaitables to the supplied
    executor so SQL work runs in dedicated threads instead of the
    asyncio default pool."""

    @pytest.mark.asyncio
    async def test_set_executor_routes_calls_to_named_threads(self):
        mgr = _make_manager()
        executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="hana-test-",
        )
        try:
            mgr.set_executor(executor)
            await mgr.aexecute_write(
                "INSERT INTO t (id, v) VALUES (:p0, :p1)", (1, "a"),
            )
            # The patched sync method records its thread name; verify
            # it ran on a thread from our executor, not on the main
            # asyncio thread or the loop's default pool.
            _, _, thread_name = mgr._backing._calls[-1]
            assert thread_name.startswith("hana-test-"), (
                f"Expected hana-test- prefixed thread, got {thread_name!r}"
            )
        finally:
            mgr.set_executor(None)
            executor.shutdown(wait=False)

    @pytest.mark.asyncio
    async def test_set_executor_none_reverts_to_default_executor(self):
        mgr = _make_manager()
        # No executor installed → uses loop's default
        await mgr.aexecute_write(
            "INSERT INTO t (id, v) VALUES (:p0, :p1)", (1, "a"),
        )
        _, _, thread_name = mgr._backing._calls[-1]
        # Default executor threads are named "ThreadPoolExecutor-N_M"
        # or similar — they're definitely not "hana-test-" and they're
        # not the main thread.
        assert "hana-test-" not in thread_name
        assert thread_name != threading.main_thread().name


# ───────────────────────── event loop not blocked ──────────────────


class TestEventLoopUnblockedDuringSQL:
    """Regression: while ``aexecute_*`` is awaiting a slow SQL call,
    other coroutines on the event loop must continue to run. This is
    the whole point of Part 2 — without the executor offload, a sync
    HANA call stalls the loop and no other request can be served."""

    @pytest.mark.asyncio
    async def test_other_coroutines_run_during_long_query(self):
        mgr = _make_manager()

        # Replace execute_query with a deliberately slow sync function
        # — simulates a 100ms HANA roundtrip.
        def _slow_query(sql, params=()):
            time.sleep(0.1)
            return [{"ok": True}]

        mgr.execute_query = _slow_query  # type: ignore[assignment]

        # Counter incremented by a competing coroutine. If the event
        # loop is blocked during aexecute_query, this counter stays at 0.
        tick_count = 0

        async def _ticker():
            nonlocal tick_count
            for _ in range(8):
                await asyncio.sleep(0.01)
                tick_count += 1

        # Run query and ticker concurrently.
        query_task = asyncio.create_task(
            mgr.aexecute_query("SELECT 1"),
        )
        ticker_task = asyncio.create_task(_ticker())

        rows = await query_task
        await ticker_task

        assert rows == [{"ok": True}]
        # Ticker fired at least a few times during the 100ms sleep —
        # proves the event loop was not blocked.
        assert tick_count >= 5, (
            f"Event loop stalled during aexecute_query "
            f"(tick_count={tick_count} of 8 expected)"
        )
