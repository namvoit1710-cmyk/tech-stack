import threading
import time
from queue import Queue
from unittest.mock import MagicMock

import pytest

from agent_sdk.layer4_frameworks.persistence.hana.hana_connection_manager import (
    HanaConnectionManager,
)


def _make_manager(pool_size=2, max_overflow=1):
    """Create a HanaConnectionManager with ALL required attributes mocked.

    Required attributes (from __init__ at lines 29-46):
      _pool_size, _max_overflow, _pool_recycle, _pool (Queue),
      _created (int), _lock (threading.Lock), _birth (dict).
    Note: there is NO _checkout_timeout attribute — _acquire uses
    hardcoded timeout=30 at line 106.
    """
    mgr = HanaConnectionManager.__new__(HanaConnectionManager)
    mgr._pool_size = pool_size
    mgr._max_overflow = max_overflow
    mgr._pool_recycle = 3600  # seconds, from settings.HANA_POOL_RECYCLE
    mgr._created = 0
    mgr._lock = threading.Lock()
    mgr._pool = Queue(maxsize=pool_size + max_overflow)
    mgr._birth = {}  # dict[int, float] — tracks connection creation time
    return mgr


def test_execute_write_raises_on_failure():
    """execute_write must re-raise exceptions after rollback, not silently return 0."""
    mgr = _make_manager()
    # Pre-populate pool with a mock connection
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.execute.side_effect = Exception("SQL error")
    mgr._pool.put(mock_conn)
    mgr._created = 1
    mgr._birth[id(mock_conn)] = time.monotonic()
    # _bind_params is a staticmethod — it works without mocking
    with pytest.raises(Exception, match="SQL error"):
        mgr.execute_write("INSERT INTO t VALUES (?)", (1,))


def test_execute_write_returns_rowcount_on_success():
    """execute_write must return the actual rowcount on success."""
    mgr = _make_manager()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.rowcount = 42
    mgr._pool.put(mock_conn)
    mgr._created = 1
    mgr._birth[id(mock_conn)] = time.monotonic()
    result = mgr.execute_write("UPDATE t SET x=1", ())
    assert result == 42


def test_initialize_created_count_matches_pool():
    """After initialize(), _created must equal the number of connections actually in the pool."""
    mgr = _make_manager(pool_size=3, max_overflow=0)
    call_count = 0

    def mock_create():
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise Exception("Connection refused")
        conn = MagicMock()
        mgr._birth[id(conn)] = time.monotonic()
        return conn

    mgr._create_connection = mock_create
    mgr.initialize()
    # 3 attempts, 1 failure → 2 created, _created must be 2
    assert (
        mgr._created == mgr._pool.qsize()
    ), f"_created={mgr._created} but pool has {mgr._pool.qsize()} connections"


def test_initialize_thread_safety():
    """Concurrent initialize() calls must not over-count _created."""
    mgr = _make_manager(pool_size=2, max_overflow=0)
    barrier = threading.Barrier(2)

    def slow_create():
        barrier.wait()  # Force concurrent execution
        conn = MagicMock()
        mgr._birth[id(conn)] = time.monotonic()
        return conn

    mgr._create_connection = slow_create
    threads = [threading.Thread(target=mgr.initialize) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # max pool_size=2, two initializes = up to 4 connections attempted,
    # but _created must never exceed pool_size + max_overflow
    max_allowed = mgr._pool_size + mgr._max_overflow
    assert (
        mgr._created <= max_allowed
    ), f"_created={mgr._created} exceeds max_allowed={max_allowed}"


def test_acquire_all_stale_raises_connection_error_not_recursion():
    """When all pooled connections are stale, _acquire must raise ConnectionError.

    Before the fix, _acquire() called itself recursively for every stale
    connection, causing RecursionError when the pool held more stale
    connections than Python's recursion limit.  After the fix it uses a
    bounded loop and raises ConnectionError instead.
    """
    pool_size = 3
    mgr = _make_manager(pool_size=pool_size, max_overflow=0)

    # Fill pool with stale connections (birth time far in the past)
    for _ in range(pool_size):
        conn = MagicMock()
        mgr._birth[id(conn)] = (
            time.monotonic() - mgr._pool_recycle - 1
        )  # age = monotonic() > recycle, so stale
        mgr._pool.put(conn)
    mgr._created = pool_size

    # Prevent fallback to _create_connection (needs real HANA settings);
    # the test validates that the bounded loop raises ConnectionError
    # after exhausting stale connections, not that it can create new ones.
    mgr._create_connection = MagicMock(side_effect=ConnectionError("No HANA in test"))

    # Must not raise RecursionError; must raise ConnectionError
    with pytest.raises(ConnectionError):
        mgr._acquire()


def test_acquire_stale_then_healthy_returns_healthy():
    """_acquire must skip stale connections and return the first healthy one."""
    pool_size = 3
    mgr = _make_manager(pool_size=pool_size, max_overflow=0)

    stale_conn = MagicMock()
    mgr._birth[id(stale_conn)] = time.monotonic() - mgr._pool_recycle - 1  # stale

    healthy_conn = MagicMock()
    mgr._birth[id(healthy_conn)] = time.monotonic()  # fresh

    mgr._pool.put(stale_conn)
    mgr._pool.put(healthy_conn)
    mgr._created = 2

    result = mgr._acquire()
    assert result is healthy_conn


def test_is_stale_returns_true_when_birth_not_tracked():
    """_is_stale must return True for a connection with no birth record.

    With the old default of 0, a system with uptime < _pool_recycle would
    incorrectly treat the unknown connection as healthy.  After the fix,
    missing birth always means stale.
    """
    mgr = _make_manager()
    conn = MagicMock()
    # Deliberately do NOT add id(conn) to mgr._birth
    assert mgr._is_stale(conn) is True
