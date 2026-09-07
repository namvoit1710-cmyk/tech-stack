"""Connection reuse and the schema cache - the two fixed costs a migration paid twice.

Both are caches, and a cache that is wrong is worse than no cache at all. These pin
what must still be true once the cost is gone: a parked connection is checked before
it is trusted, a connection whose job died is never handed to the next one, and the
schema stops being believed soon enough that a rebuilt source is still caught upfront.
"""

import time

import pytest

from app.layer2_application.features.data_migration import df_migration_job as mod


class _Conf:
    host, port, user = "h", 443, "u"
    encrypt, validate_cert = True, False


class _Cursor:
    def __init__(self, connection, columns=("A", "B")):
        self.connection, self.columns = connection, columns
        self.description = None

    def execute(self, sql):
        self.connection.executed.append(sql)
        if self.connection.dead:
            raise RuntimeError("connection is closed")
        if "LIMIT 0" in sql:
            self.description = [(c,) for c in self.columns]
        return True

    def fetchone(self):
        return (1,)

    def close(self):
        pass


class _Connection:
    def __init__(self, dead=False, columns=("A", "B")):
        self.dead, self.closed, self.rolled_back = dead, False, False
        self.executed, self.columns = [], columns

    def cursor(self):
        return _Cursor(self, self.columns)

    def rollback(self):
        self.rolled_back = True

    def commit(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def clean_state():
    """The pool and the cache are module-level; leaking them across tests would make
    the failures depend on ordering."""
    mod._POOL.clear()
    mod._SCHEMA_CACHE.clear()
    yield
    mod._POOL.clear()
    mod._SCHEMA_CACHE.clear()


# ------------------------------------------------------------- the connection pool

def test_a_parked_connection_is_reused_instead_of_dialling_again(monkeypatch):
    monkeypatch.setattr(mod.hana_reader, "connect",
                        lambda **kw: pytest.fail("should not have connected"))
    parked = _Connection()
    mod.release_connection(_Conf, parked, reuse=True)
    assert mod.acquire_connection(_Conf, "pw") is parked


def test_a_connection_that_died_while_parked_is_not_handed_out(monkeypatch):
    """Server restarts and dropped NATs happen; the next job must not inherit one."""
    fresh = _Connection()
    monkeypatch.setattr(mod.hana_reader, "connect", lambda **kw: fresh)
    corpse = _Connection(dead=True)
    mod.release_connection(_Conf, corpse, reuse=True)
    assert mod.acquire_connection(_Conf, "pw") is fresh
    assert corpse.closed


def test_a_connection_parked_too_long_is_dropped(monkeypatch):
    fresh = _Connection()
    monkeypatch.setattr(mod.hana_reader, "connect", lambda **kw: fresh)
    stale = _Connection()
    mod.release_connection(_Conf, stale, reuse=True)
    mod._POOL[mod._pool_key(_Conf)] = [(stale, time.time() - mod.POOL_IDLE_SEC - 1)]
    assert mod.acquire_connection(_Conf, "pw") is fresh
    assert stale.closed


def test_a_failed_job_closes_its_connection_rather_than_parking_it():
    """`reuse=False` is what the finally block passes on any abnormal exit."""
    connection = _Connection()
    mod.release_connection(_Conf, connection, reuse=False)
    assert connection.closed
    assert not mod._POOL.get(mod._pool_key(_Conf))


def test_a_connection_is_rolled_back_before_it_is_parked():
    """Otherwise the next job starts inside someone else's open transaction."""
    connection = _Connection()
    mod.release_connection(_Conf, connection, reuse=True)
    assert connection.rolled_back


def test_releasing_never_raises():
    """It runs in a finally block: an exception here would replace the job's real
    outcome with a plumbing error."""
    class Awkward(_Connection):
        def rollback(self):
            raise RuntimeError("no")

    connection = Awkward()
    mod.release_connection(_Conf, connection, reuse=True)   # must not raise
    assert connection.closed
    mod.release_connection(_Conf, None, reuse=True)          # nor on nothing at all


def test_two_jobs_never_hold_the_same_connection(monkeypatch):
    """Checked out exclusively - two jobs sharing one would commit each other's DDL."""
    monkeypatch.setattr(mod.hana_reader, "connect", lambda **kw: _Connection())
    first = _Connection()
    mod.release_connection(_Conf, first, reuse=True)
    a = mod.acquire_connection(_Conf, "pw")
    b = mod.acquire_connection(_Conf, "pw")
    assert a is first and b is not a


# ------------------------------------------------------------------ the pool's cap

def test_a_burst_does_not_leave_a_connection_parked_for_every_job():
    """Uncapped, 40 concurrent jobs left 40 idle sessions on a shared database."""
    burst = [_Connection() for _ in range(mod.POOL_MAX + 6)]
    for connection in burst:
        mod.release_connection(_Conf, connection, reuse=True)
    assert len(mod._POOL[mod._pool_key(_Conf)]) == mod.POOL_MAX
    assert sum(c.closed for c in burst) == 6


def test_the_surplus_is_closed_not_dropped_on_the_floor():
    """A connection that is neither parked nor closed is a leaked HANA session."""
    monkey = [_Connection() for _ in range(mod.POOL_MAX + 1)]
    for connection in monkey:
        mod.release_connection(_Conf, connection, reuse=True)
    parked = {id(c) for c, _ in mod._POOL[mod._pool_key(_Conf)]}
    for connection in monkey:
        assert (id(connection) in parked) or connection.closed


def test_the_cap_never_makes_a_job_wait(monkeypatch):
    """POOL_MAX bounds what is kept warm, not what can be in use. A job that finds
    the pool empty dials its own connection rather than queueing behind the cap."""
    dialled = []

    def connect(**kw):
        fresh = _Connection()
        dialled.append(fresh)
        return fresh

    monkeypatch.setattr(mod.hana_reader, "connect", connect)
    held = [mod.acquire_connection(_Conf, "pw") for _ in range(mod.POOL_MAX + 10)]
    assert len(held) == mod.POOL_MAX + 10
    assert len(set(map(id, held))) == len(held)   # all distinct; nobody shared


def test_the_cap_is_tunable_without_a_code_change():
    """deployment.yml sets DF_POOL_MAX; steady-state concurrency differs per tenant."""
    assert mod.POOL_MAX == int(__import__("os").environ.get("DF_POOL_MAX", "4"))


def test_the_idle_ttl_sits_under_the_measured_survival_time():
    """An idle connection was seen reset at ~26 min. Parking past that just means
    every checkout probes a corpse first."""
    assert mod.POOL_IDLE_SEC <= 26 * 60


# --------------------------------------------------------------- the schema cache

def test_the_schema_is_read_once_and_then_remembered():
    connection = _Connection(columns=("MANDT", "MATNR"))
    cursor = connection.cursor()
    assert mod.table_columns(cursor, _Conf, "VT") == ["MANDT", "MATNR"]
    assert mod.table_columns(cursor, _Conf, "VT") == ["MANDT", "MATNR"]
    assert len([s for s in connection.executed if "LIMIT 0" in s]) == 1


def test_the_schema_is_re_read_once_the_ttl_has_passed():
    """A virtual table does get rebuilt against a changed source. Believing a stale
    column list would push the failure past the upfront check and into the read."""
    connection = _Connection(columns=("MANDT",))
    cursor = connection.cursor()
    mod.table_columns(cursor, _Conf, "VT")
    key = (_Conf.host, _Conf.port, _Conf.user, "VT")
    mod._SCHEMA_CACHE[key] = (["MANDT"], time.time() - mod.SCHEMA_TTL_SEC - 1)
    mod.table_columns(cursor, _Conf, "VT")
    assert len([s for s in connection.executed if "LIMIT 0" in s]) == 2


def test_different_tables_do_not_share_a_cache_entry():
    connection = _Connection(columns=("A",))
    cursor = connection.cursor()
    mod.table_columns(cursor, _Conf, "VT_ONE")
    mod.table_columns(cursor, _Conf, "VT_TWO")
    assert len([s for s in connection.executed if "LIMIT 0" in s]) == 2


def test_the_caller_cannot_mutate_the_cached_schema():
    """It hands out the list that the next job will validate its rules against."""
    connection = _Connection(columns=("A", "B"))
    cursor = connection.cursor()
    mod.table_columns(cursor, _Conf, "VT").append("INVENTED")
    assert mod.table_columns(cursor, _Conf, "VT") == ["A", "B"]


# ----------------------------------------------------------------- the scaffolding

def test_dropping_scaffolding_survives_a_table_that_is_not_there():
    """Called on paths where the table may never have been created."""
    connection = _Connection(dead=True)
    mod._drop(connection.cursor(), connection, "DF_SRC_NOPE")   # must not raise
