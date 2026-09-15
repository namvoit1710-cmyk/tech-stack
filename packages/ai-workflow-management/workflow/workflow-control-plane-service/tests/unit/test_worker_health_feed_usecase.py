"""SA-2056 (R9) — the CP differ, rekeyed to worker_app by SA-2047 R14 Task 15.
Fake registry client + fake broker; asyncio_mode=auto."""
from app.layer2_application.features.worker_health_feed import WorkerHealthFeedUseCase


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass
    def exception(self, *a, **k): pass


class _Client:
    """Returns a scripted sequence of get_app_summary() results, one per tick."""
    def __init__(self, *scripted):
        self._script = list(scripted)
        self.calls = 0

    def get_app_summary(self):
        r = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        return r


class _Broker:
    def __init__(self):
        self.published = []            # list of (topic, envelope)

    def publish_to_topic(self, topic, message, key=None):
        self.published.append((topic, message))


def _row(app, nodes_total, nodes_healthy, *, replicas_total=None, replicas_healthy=None,
         replica_identity="known", mode="push", age=1.0, node_types=None):
    """One /workers/apps row (Task 13's ``get_app_summary`` shape). Replica
    counts default to mirroring the node counts so callers that don't care
    about the nodes-vs-replicas split (most tests here -- they're exercising
    the differ, not Task 13's counting rule) can pass just two numbers, same
    as the old worker_type ``_row`` helper did."""
    return {
        "worker_app": app,
        "delivery_mode": mode,
        "nodes_total": nodes_total,
        "nodes_healthy": nodes_healthy,
        "replicas_total": nodes_total if replicas_total is None else replicas_total,
        "replicas_healthy": nodes_healthy if replicas_healthy is None else replicas_healthy,
        "replica_identity": replica_identity,
        "oldest_heartbeat_age_seconds": age,
        # Defaults to one synthetic name per node so a row is self-consistent
        # (len(node_types) == nodes_total) without every caller spelling them out.
        "node_types": [f"{app}_node_{i}" for i in range(nodes_total)]
        if node_types is None else node_types,
    }


def _uc(*scripted, broker=None):
    uc = WorkerHealthFeedUseCase(worker_registry_client=_Client(*scripted), logger=_Log())
    uc._broker = broker if broker is not None else _Broker()
    return uc


async def test_cold_start_first_tick_publishes_nothing():
    """The first tick seeds the baseline silently — R10 gets full state from R6's
    snapshot-on-connect; replaying the registry as deltas would flood the room on
    every CP restart."""
    b = _Broker()
    uc = _uc([_row("jira", 3, 3)], broker=b)
    await uc.execute()
    assert b.published == []


async def test_health_drop_publishes_one_delta():
    b = _Broker()
    uc = _uc([_row("jira", 3, 3)], [_row("jira", 3, 2)], broker=b)
    await uc.execute()                          # tick 1: seed, silent
    await uc.execute()                          # tick 2: 3->2 healthy
    assert len(b.published) == 1
    topic, env = b.published[0]
    assert topic == "realtime.events"
    assert env["event_type"] == "worker.app.health"
    assert env["rooms"] == ["workers:health"]
    assert not env["_run_key"] and not env["_run_seq"]     # bypass resequencing
    p = env["payload"]
    # Full payload shape — would fail if any field were dropped or renamed.
    # node_types rides along because the FE replaces the whole row on a patch;
    # omit it and every realtime tick would strip the node list off the row.
    assert set(p.keys()) == {
        "_event", "worker_app", "delivery_mode", "nodes_total", "nodes_healthy",
        "node_types", "replicas_total", "replicas_healthy", "replica_identity",
        "oldest_heartbeat_age_seconds", "at",
    }
    assert p["_event"] == "worker.app.health"
    assert (p["worker_app"], p["replicas_total"], p["replicas_healthy"]) == ("jira", 3, 2)
    assert (p["nodes_total"], p["nodes_healthy"]) == (3, 2)
    assert p["node_types"] == ["jira_node_0", "jira_node_1", "jira_node_2"]
    assert p["delivery_mode"] == "push"
    assert p["replica_identity"] == "known"
    assert "at" in p


async def test_no_change_publishes_nothing():
    b = _Broker()
    uc = _uc([_row("jira", 3, 3)], [_row("jira", 3, 3)], broker=b)
    await uc.execute(); await uc.execute()
    assert b.published == []


async def test_node_type_swap_publishes_even_though_counts_are_unchanged():
    """One type retired as another appears: every count stays put, so the
    catalogue itself has to be in the change-detection tuple or the swap goes
    out unannounced and the popover keeps listing a node that no longer exists."""
    b = _Broker()
    uc = _uc(
        [_row("jira", 2, 2, node_types=["jira_create_issue", "jira_get_issue"])],
        [_row("jira", 2, 2, node_types=["jira_create_issue", "jira_search"])],
        broker=b,
    )
    await uc.execute()
    await uc.execute()
    assert len(b.published) == 1
    assert b.published[0][1]["payload"]["node_types"] == ["jira_create_issue", "jira_search"]


async def test_tombstone_empties_the_node_type_list():
    b = _Broker()
    uc = _uc([_row("jira", 2, 2)], [], broker=b)
    await uc.execute()
    await uc.execute()
    assert b.published[0][1]["payload"]["node_types"] == []


async def test_vanished_app_tombstones_total_zero():
    b = _Broker()
    uc = _uc([_row("jira", 2, 2)], [], broker=b)   # tick 2: genuine empty 200
    await uc.execute()                             # seed
    await uc.execute()                             # jira gone -> tombstone
    assert len(b.published) == 1
    p = b.published[0][1]["payload"]
    assert p["worker_app"] == "jira"
    assert p["nodes_total"] == 0 and p["nodes_healthy"] == 0
    assert p["replicas_total"] == 0 and p["replicas_healthy"] == 0


async def test_unreachable_read_publishes_nothing_and_keeps_snapshot():
    """None (transport failure) must NOT tombstone. The risk-review case."""
    b = _Broker()
    uc = _uc([_row("jira", 3, 3)], None, [_row("jira", 3, 1)], broker=b)
    await uc.execute()      # seed jira=3/3
    await uc.execute()      # None -> skip, keep snapshot, publish nothing
    assert b.published == []
    await uc.execute()      # 3->1 vs the KEPT snapshot -> one delta
    assert len(b.published) == 1
    assert b.published[0][1]["payload"]["replicas_healthy"] == 1


async def test_genuine_empty_after_seed_tombstones_not_silent():
    """[] (real empty 200) IS a state change — distinct from None."""
    b = _Broker()
    uc = _uc([_row("a", 1, 1)], [], broker=b)
    await uc.execute()      # seed a
    await uc.execute()      # [] -> a tombstoned
    assert len(b.published) == 1 and b.published[0][1]["payload"]["worker_app"] == "a"


async def test_seeded_flag_guards_restore_after_genuine_drain():
    """``_seeded`` (not ``_last``) is what marks "execute() has run before".
    A mutant that checks ``if not self._last`` instead cannot tell "never
    ran" (``_last`` == {}) apart from "ran, registry genuinely drained to {}"
    (``_last`` == {} too) -- on the tick right after a real drain it would
    silently re-seed off the empty snapshot and swallow the restore delta
    that follows, so the dashboard would never learn the workers came back."""
    b = _Broker()
    uc = _uc([_row("jira", 3, 3)], [], [_row("jira", 3, 3)], broker=b)
    await uc.execute()      # tick 1: seed jira=3/3, silent
    await uc.execute()      # tick 2: [] -> tombstone jira
    await uc.execute()      # tick 3: jira reappears -> restore delta
    assert len(b.published) == 2
    tombstone = b.published[0][1]["payload"]
    restore = b.published[1][1]["payload"]
    assert tombstone["worker_app"] == "jira"
    assert tombstone["replicas_total"] == 0 and tombstone["replicas_healthy"] == 0
    assert restore["worker_app"] == "jira"
    assert restore["replicas_total"] == 3 and restore["replicas_healthy"] == 3


async def test_no_broker_is_a_noop():
    uc = WorkerHealthFeedUseCase(worker_registry_client=_Client([_row("jira", 3, 3)]), logger=_Log())
    uc._broker = None
    out = await uc.execute()
    assert out.get("published", 0) == 0


async def test_replica_identity_change_alone_publishes_a_delta():
    """A pending -> known transition is a state change the page must see, even
    when every count happens to stay put on that same tick, so
    ``replica_identity`` must be IN the change-detection tuple.

    In production a pending app is FORCED to report 0/0 replicas upstream
    (see ``ListWorkerAppsUseCase`` in the worker-executor-service: pending
    zeroes ``replicas_total``/``replicas_healthy``), so a real pending->known
    flip almost always co-occurs with a replica-count jump that the tuple
    would already catch via ``replicas_total``/``replicas_healthy`` -- making
    a naive test non-discriminating for ``replica_identity`` specifically.
    This test holds nodes_total, nodes_healthy, replicas_total,
    replicas_healthy, and delivery_mode ALL fixed across the two ticks and
    flips ONLY replica_identity, so it fails if replica_identity is ever
    dropped from (or never added to) the tuple -- exactly the property Task
    15 must add."""
    b = _Broker()
    uc = _uc(
        [_row("jira", 3, 3, replica_identity="pending")],
        [_row("jira", 3, 3, replica_identity="known")],
        broker=b,
    )
    await uc.execute()      # tick 1: seed (pending)
    await uc.execute()      # tick 2: identity flips, every count identical
    assert len(b.published) == 1
    p = b.published[0][1]["payload"]
    assert p["worker_app"] == "jira"
    assert p["replica_identity"] == "known"
    assert p["nodes_total"] == 3 and p["nodes_healthy"] == 3
    assert p["replicas_total"] == 3 and p["replicas_healthy"] == 3


async def test_age_change_alone_does_not_publish():
    """Age (``oldest_heartbeat_age_seconds``) drifts on essentially every real
    tick as heartbeats keep arriving. If it were folded into the
    change-detection tuple the feed would publish on every single tick,
    forever, defeating the entire point of diffing (and flooding
    ``workers:health``). This test holds nodes_total, nodes_healthy,
    replicas_total, replicas_healthy, delivery_mode, and replica_identity ALL
    fixed across the two ticks and moves ONLY the age, so it fails if age is
    ever folded into the tuple."""
    b = _Broker()
    uc = _uc(
        [_row("jira", 3, 3, age=1.0)],
        [_row("jira", 3, 3, age=987.0)],
        broker=b,
    )
    await uc.execute()      # tick 1: seed
    await uc.execute()      # tick 2: only age moved
    assert b.published == []
