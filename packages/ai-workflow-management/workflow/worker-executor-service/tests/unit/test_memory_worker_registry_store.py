"""SA-2049 (R2) — the registry keys by worker.id, the (instance_id, worker_type)
PAIR -- not by instance_id alone and not by worker_type alone.

This is the FIRST direct unit test of MemoryWorkerRegistryStore. Until now the
store was only exercised indirectly through 15 use-case test files, which is how
the replica collapse survived: register() reused the previous instance's id and
popped its row, so N replicas of one worker_type became ONE entry that all N
heartbeated (evidence map §2, §16.1).
"""

import sys
import os
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer4_frameworks.repositories.memory_worker_registry_store import (
    MemoryWorkerRegistryStore,
)


def _reg(instance_id: str, worker_type: str = "http-request-worker",
         health: str = "healthy", hb: str = "2026-07-17T09:00:00+00:00") -> WorkerRegistration:
    """One replica. endpoint_url is a CF route -> identical across replicas (§11)."""
    return WorkerRegistration(
        instance_id=instance_id,
        worker_type=worker_type,
        endpoint_url="https://smdg-ai-dev-worker-http-request-worker.cfapps.br10.hana.ondemand.com",
        health_status=health,
        last_heartbeat=hb,
    )


class TestDistinctInstancesSameType:
    """Same worker_type, distinct instance_id per registration -- the pair still
    differs, so each registration gets its own row (contrast TestTheKeyIsThePair,
    which holds instance_id fixed and varies worker_type instead)."""

    def test_n_instances_produce_n_entries(self):
        """AC: N instances = N entries."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("guid-1"))
        store.register(_reg("guid-2"))
        store.register(_reg("guid-3"))
        assert len(store.list_all()) == 3

    def test_heartbeat_updates_only_its_own_registration(self):
        """THE test that proves the collapse is dead.

        Before R2 all replicas shared one row, so a live replica kept a dead
        replica's row fresh and nothing could ever be marked individually dead.

        heartbeat() takes the REGISTRATION id (the wire's `worker_id`), which R1
        derives as "<instance_id>:<worker_type>" -- not the bare instance_id.
        """
        store = MemoryWorkerRegistryStore()
        a = _reg("guid-1", hb="2026-07-17T09:00:00+00:00")
        b = _reg("guid-2", hb="2026-07-17T09:00:00+00:00")
        store.register(a)
        store.register(b)

        assert store.heartbeat(a.id) is True

        assert store.get(a.id).last_heartbeat != "2026-07-17T09:00:00+00:00"   # moved
        assert store.get(b.id).last_heartbeat == "2026-07-17T09:00:00+00:00"   # untouched

    def test_deregister_removes_only_its_own_registration(self):
        """The rolling-deploy outage test.

        Before R2: B stole A's id, then A's graceful shutdown deregistered that id
        and deleted B's row -- the node vanished from the palette until B's next
        heartbeat re-registered it (<=30s).

        deregister() takes the REGISTRATION id, so it retires exactly one
        (process, worker_type) pair -- a multi-type worker's other types stay up.
        """
        store = MemoryWorkerRegistryStore()
        a = _reg("guid-A")
        b = _reg("guid-B")
        store.register(a)
        store.register(b)

        store.deregister(a.id)

        remaining = store.list_all()
        assert len(remaining) == 1
        assert remaining[0].instance_id == "guid-B"

    def test_concurrent_register_same_type(self):
        """Two replicas racing register() must both land — no lost update."""
        store = MemoryWorkerRegistryStore()
        barrier = threading.Barrier(2)

        def go(iid: str):
            barrier.wait()
            store.register(_reg(iid))

        t1 = threading.Thread(target=go, args=("guid-1",))
        t2 = threading.Thread(target=go, args=("guid-2",))
        t1.start(); t2.start(); t1.join(); t2.join()

        assert len(store.list_all()) == 2


class TestGetByWorkerType:
    def test_picks_a_healthy_one(self):
        """AC: get_by_worker_type picks a healthy one."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("guid-1", health="unhealthy"))
        store.register(_reg("guid-2", health="healthy"))
        store.register(_reg("guid-3", health="unhealthy"))
        got = store.get_by_worker_type("http-request-worker")
        assert got is not None
        assert got.instance_id == "guid-2"

    def test_picks_the_freshest_healthy(self):
        store = MemoryWorkerRegistryStore()
        store.register(_reg("guid-1", hb="2026-07-17T09:00:00+00:00"))
        store.register(_reg("guid-2", hb="2026-07-17T09:00:30+00:00"))
        store.register(_reg("guid-3", hb="2026-07-17T09:00:15+00:00"))
        assert store.get_by_worker_type("http-request-worker").instance_id == "guid-2"

    def test_mixed_timestamp_formats(self):
        """The price of the freshest-wins rule. Producers write ...+00:00, ...Z,
        HANA's space-separated form, and blank. String compare picks a corpse."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("guid-zulu", hb="2026-07-17T09:00:30Z"))          # freshest
        store.register(_reg("guid-offset", hb="2026-07-17T09:00:00+00:00"))
        store.register(_reg("guid-hana", hb="2026-07-17 09:00:15"))
        store.register(_reg("guid-blank", hb=""))
        assert store.get_by_worker_type("http-request-worker").instance_id == "guid-zulu"

    def test_blank_heartbeat_never_wins(self):
        store = MemoryWorkerRegistryStore()
        store.register(_reg("guid-blank", hb=""))
        store.register(_reg("guid-real", hb="2026-07-17T09:00:00+00:00"))
        assert store.get_by_worker_type("http-request-worker").instance_id == "guid-real"

    def test_returns_none_when_all_unhealthy(self):
        """A type present but fully dead. Dispatch already guards `if executor:`."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("guid-1", health="unhealthy"))
        store.register(_reg("guid-2", health="unhealthy"))
        assert store.get_by_worker_type("http-request-worker") is None

    def test_blank_status_resolves_alive_mirroring_the_palette(self):
        """A gRPC heartbeat that omits status writes health_status="" (proto3
        default); a NULL HANA column rehydrates as None. The control-plane
        palette's _HEALTHY_STATUSES treats both as alive, so get_by_worker_type
        and count_healthy must too -- else the executor unlists a node the palette
        shows, a split brain. Would fail if _HEALTHY dropped the blank/None members
        (as it did before this fix)."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("guid-1", health=""))
        store.register(_reg("guid-2", health=None))
        assert store.get_by_worker_type("http-request-worker") is not None
        assert store.count_healthy("http-request-worker") == 2

    def test_degraded_does_not_resolve(self):
        """"degraded" is in the proto's status vocabulary but is excluded here AND
        by the palette (coherent). Whether a degraded worker should still resolve
        is SA-2051's call. Would fail if someone widened _HEALTHY to include it."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("guid-1", health="degraded"))
        assert store.get_by_worker_type("http-request-worker") is None
        assert store.count_healthy("http-request-worker") == 0

    def test_returns_none_for_unknown_type(self):
        assert MemoryWorkerRegistryStore().get_by_worker_type("nope") is None

    def test_returns_none_for_blank_type(self):
        store = MemoryWorkerRegistryStore()
        store.register(_reg("guid-1"))
        assert store.get_by_worker_type("") is None


class TestAggregateByType:
    def _seeded(self) -> MemoryWorkerRegistryStore:
        store = MemoryWorkerRegistryStore()
        store.register(_reg("guid-1", health="healthy"))
        store.register(_reg("guid-2", health="healthy"))
        store.register(_reg("guid-3", health="healthy"))
        store.register(_reg("guid-4", health="unhealthy"))
        store.register(_reg("guid-5", health="unhealthy"))
        store.register(_reg("teams-1", worker_type="ms-teams-worker", health="healthy"))
        return store

    def test_list_instances_returns_all_including_unhealthy(self):
        got = self._seeded().list_instances("http-request-worker")
        assert len(got) == 5
        assert {w.instance_id for w in got} == {"guid-1", "guid-2", "guid-3", "guid-4", "guid-5"}

    def test_list_instances_unknown_type_is_empty(self):
        assert self._seeded().list_instances("nope") == []

    def test_list_instances_blank_type_is_empty(self):
        assert self._seeded().list_instances("") == []

    def test_count_healthy(self):
        store = self._seeded()
        assert store.count_healthy("http-request-worker") == 3
        assert store.count_healthy("ms-teams-worker") == 1
        assert store.count_healthy("nope") == 0

    def test_replica_summary_counts(self):
        """AC: unit tests for aggregate-by-type. '5 replicas, 3 healthy'."""
        summary = self._seeded().replica_summary()
        assert summary["http-request-worker"] == {"total": 5, "healthy": 3, "unhealthy": 2}
        assert summary["ms-teams-worker"] == {"total": 1, "healthy": 1, "unhealthy": 0}

    def test_replica_summary_is_empty_for_empty_store(self):
        assert MemoryWorkerRegistryStore().replica_summary() == {}

    def test_count_healthy_counts_processes_not_rows(self):
        """count_healthy(T) answers 'how many PROCESSES serve T?' -- DISTINCT
        instance_id, not rows.

        register() cannot produce two rows for one (process, type) pair, so rows and
        processes coincide and this cannot be provoked through the public API
        (spec §3.2). The store is seeded directly to pin the CONTRACT: R3's HANA
        store shares this Protocol and CAN hold a duplicate -- a legacy row keyed by
        worker_type (hana_worker_registry_store.py still carries the upsert-by-type
        defect), or a racing upsert -- and there the two answers diverge.
        """
        store = MemoryWorkerRegistryStore()
        row = _reg("p0", worker_type="http-request-worker")
        dup = _reg("p0", worker_type="http-request-worker")
        store._store[row.id] = row
        store._store["legacy-row-keyed-by-worker-type"] = dup   # same process+type

        assert len(store.list_instances("http-request-worker")) == 2   # two ROWS
        assert store.count_healthy("http-request-worker") == 1         # ONE process

    def test_replica_summary_counts_processes_not_rows(self):
        """Same contract as count_healthy: total/healthy are DISTINCT instance_id.

        Seeded directly for the same reason (spec §3.2) -- see
        test_count_healthy_counts_processes_not_rows.
        """
        store = MemoryWorkerRegistryStore()
        row = _reg("p0", worker_type="http-request-worker")
        dup = _reg("p0", worker_type="http-request-worker")
        store._store[row.id] = row
        store._store["legacy-row-keyed-by-worker-type"] = dup   # same process+type

        assert store.replica_summary()["http-request-worker"] == {
            "total": 1, "healthy": 1, "unhealthy": 0,
        }


class TestTheKeyIsThePair:
    """spec §4 — the key is the PAIR (process x worker_type), not either half.

    Rev 2 keyed by instance_id and destroyed 21 of jira-worker's 22 node types
    (keystone §3.1). Keying by worker_type (the shipped bug) collapses replicas.
    Both errors take one half of a pair and call it the whole.
    """

    def test_one_process_serving_22_types_keeps_all_22(self):
        """jira-worker: 'every Jira operation registers as its OWN node.'

        Keyed by instance_id this collapses to ONE row -- 21 of 22 Jira nodes
        vanish from the catalog and dispatch for them fails outright.
        """
        store = MemoryWorkerRegistryStore()
        for i in range(22):
            store.register(_reg("jira-worker_0", worker_type=f"jira_t{i}"))

        assert len(store.list_all()) == 22
        assert all(store.get_by_worker_type(f"jira_t{i}") is not None for i in range(22))

    def test_three_replicas_of_22_types_are_66_rows(self):
        """spec §4: 3 x 22 -> 66 rows, and each type reports total == 3."""
        store = MemoryWorkerRegistryStore()
        for replica in range(3):
            for i in range(22):
                store.register(_reg(f"jira-worker_{replica}", worker_type=f"jira_t{i}"))

        assert len(store.list_all()) == 66
        assert store.replica_summary()["jira_t0"]["total"] == 3

    def test_same_process_and_type_twice_is_one_row_second_wins(self):
        """spec §4: a pod restart re-registers the same PAIR, so it overwrites its
        OWN row instead of orphaning one. That is what demotes R4 from load-bearing
        to housekeeping (keystone §5)."""
        store = MemoryWorkerRegistryStore()
        store.register(_reg("jira-worker_0", worker_type="jira_t0", health="unhealthy"))
        store.register(_reg("jira-worker_0", worker_type="jira_t0", health="healthy"))

        assert len(store.list_all()) == 1
        assert store.list_all()[0].health_status == "healthy"      # second wins

    def test_blank_instance_id_gives_one_row_per_register_call(self):
        """spec §4 / keystone §4.2: with no instance_id the SDK cannot say which
        process it is, so each registration IS its own instance -- N calls, N rows.
        This IS an observable change from the shipped upsert-by-worker_type (which
        gave 1 row); it is safe because no SDK sends an instance_id yet, so no live
        caller reaches the pair-key branch until SA-2055."""
        store = MemoryWorkerRegistryStore()
        for _ in range(5):
            store.register(_reg("", worker_type="http-request-worker"))

        assert len(store.list_all()) == 5
        assert store.count_healthy("http-request-worker") == 5
