"""In-memory implementation of IWorkerRegistryStore.

Keyed by ``worker.id`` -- the (instance_id, worker_type) PAIR, i.e. one
registration. Keying by ``worker_type`` alone (a class) collapses every replica
of a type into one shared row. Keying by ``instance_id`` alone (a process)
collapses every ``worker_type`` that process registers into one shared row --
one process can register many distinct types. Neither half is the whole key.

Thread-safe: the broker poll thread(s) read via ``get_by_worker_type`` (worker
lookup on dispatch) while FastAPI's sync-route threadpool mutates the store via
``register`` / ``deregister`` / ``heartbeat``. Without a lock, iterating
``_store.values()`` during a concurrent insert/delete raises "dictionary changed
size during iteration". The lock stays reentrant because the aggregate readers
(``count_healthy``) call ``list_instances`` while already holding it.
"""
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from app.layer1_domain.entities.worker_registration import WorkerRegistration
from app.layer1_domain.value_objects import app_instance_aggregation
from app.layer1_domain.value_objects.worker_liveness import HEALTHY_STATUSES, parse_ts


@dataclass
class SweepResult:
    """What one liveness sweep changed. `emptied` is the (worker_type, spec_version)
    pairs this sweep drove from >0 healthy to 0 -- the catalog.changed(deregistered)
    trigger. marked_count / removed_count are for the use case's log + return summary.
    """
    marked_count: int = 0
    removed_count: int = 0
    emptied: list[tuple[str, str]] = field(default_factory=list)  # (worker_type, spec_version)


class MemoryWorkerRegistryStore:
    """In-memory worker registry for development and testing."""

    def __init__(self, max_registered: int = 0, stale_after_seconds: int = 120) -> None:
        self._store: dict[str, WorkerRegistration] = {}
        self._lock = threading.RLock()
        # B6: cap the registry (untrusted-write OOM vector — a rogue worker can
        # register many DISTINCT worker_types). 0 = unlimited (default → no change).
        self._max_registered = max(0, int(max_registered or 0))
        # A worker not heartbeated within this window is evictable. Live workers
        # are NEVER evicted (see register) so a flood can't oust a victim.
        self._stale_after = max(1, int(stale_after_seconds or 120))

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def register(self, worker: WorkerRegistration) -> None:
        with self._lock:
            # The key is worker.id -- the (instance_id, worker_type) PAIR, i.e. one
            # registration. Keying by worker_type alone (a class) collapsed every
            # replica of a type into one shared row. Keying by instance_id alone (a
            # process) collapsed every worker_type that process registers into one
            # shared row -- one process can register many distinct types. Neither
            # half is the whole key. Re-registering the same id (same process, same
            # type -- e.g. a pod restart) overwrites its own row; any other pair
            # gets its own row.
            # Registry cap: when a NEW registration would exceed the cap, evict only
            # a STALE worker (not heartbeated within the window). NEVER evict a live
            # worker — otherwise an attacker flooding fresh distinct types could oust
            # a victim, whose tasks would then silently mis-route to push. If every
            # slot holds a live worker, REJECT the new registration (backpressure) —
            # MAX_REGISTERED_WORKERS caps REGISTRATIONS (worker.id rows), not worker
            # processes: one process registers one row per worker_type it serves, so
            # e.g. one jira-worker replica alone consumes 22 slots and 3 replicas
            # consume 66. Size the cap by (processes x worker_types-per-process), not
            # by process/worker count, or it will be undersized by that factor.
            # Re-deriving the right default is SA-2058's job.
            if (self._max_registered and worker.id not in self._store
                    and len(self._store) >= self._max_registered):
                cutoff = (self._now() - timedelta(seconds=self._stale_after)).isoformat()
                stale = [k for k, w in self._store.items() if (w.last_heartbeat or "") < cutoff]
                if not stale:
                    return  # registry full of live workers → refuse to add
                stalest = min(stale, key=lambda k: self._store[k].last_heartbeat)
                self._store.pop(stalest, None)
            self._store[worker.id] = worker

    def heartbeat(self, worker_id: str, status: str = "healthy") -> bool:
        with self._lock:
            worker = self._store.get(worker_id)
            if worker:
                worker.last_heartbeat = datetime.now(timezone.utc).isoformat()
                worker.health_status = status
                return True
            return False

    def discover(self, capability: str) -> list[WorkerRegistration]:
        with self._lock:
            if not capability:
                return list(self._store.values())

            results = []
            for worker in self._store.values():
                for cap in worker.capabilities:
                    if cap.domain == capability or cap.action == capability:
                        results.append(worker)
                        break
            return results

    def deregister(self, worker_id: str) -> None:
        with self._lock:
            self._store.pop(worker_id, None)

    def get(self, worker_id: str) -> WorkerRegistration | None:
        with self._lock:
            return self._store.get(worker_id)

    def get_by_worker_type(self, worker_type: str) -> WorkerRegistration | None:
        """The freshest HEALTHY instance of a type, or None (SA-2049).

        Callers read only type-level fields — and ``endpoint_url`` is a CF route,
        identical across every replica — so *which* instance comes back does not
        affect dispatch; CF's router picks the pod. Health decides whether the type
        resolves at all. Freshness is compared via ``parse_ts``, never as raw
        strings (the values arrive in several formats).
        """
        if not worker_type:
            return None
        with self._lock:
            candidates = [
                w for w in self._store.values()
                if w.worker_type == worker_type and w.health_status in HEALTHY_STATUSES
            ]
            if not candidates:
                return None
            return max(candidates, key=lambda w: parse_ts(w.last_heartbeat))

    def list_instances(self, worker_type: str) -> list[WorkerRegistration]:
        """Every registration of a type — healthy or not. The honest answer.

        Rows here ARE processes: *within a single worker_type* each process
        contributes exactly one row (its ``<instance_id>:<worker_type>``
        registration), so len() of this is the replica count of that type. That does
        NOT generalise store-wide — one process serving 22 types owns 22 rows —
        which is why ``count_healthy`` / ``replica_summary`` count DISTINCT
        ``instance_id`` rather than rows (SA-2049 §3.2).
        """
        if not worker_type:
            return []
        with self._lock:
            return [w for w in self._store.values() if w.worker_type == worker_type]

    def count_healthy(self, worker_type: str) -> int:
        """How many PROCESSES of this type currently report healthy (SA-2049).

        Counts DISTINCT ``instance_id``, never rows: "how many replicas are alive?"
        and a replica is a process. Within one worker_type a process contributes
        exactly one row, so this equals the row count today — but it is what the
        question MEANS, and it stays correct if that ever stops holding (R3's HANA
        store, which can carry a legacy row keyed by worker_type).
        """
        with self._lock:
            return len({
                w.instance_id for w in self.list_instances(worker_type)
                if w.health_status in HEALTHY_STATUSES
            })

    def replica_summary(self) -> dict[str, dict[str, int]]:
        """``{worker_type: {"total": N, "healthy": H, "unhealthy": N-H}}``.

        The read behind /workers/replicas (R6), the replica gauges (R7), the
        realtime feed (R9) and the /workers page (R10) — i.e. the method that makes
        "5 replicas, 3 healthy" expressible at all.

        Counts PROCESSES, not rows (SA-2049): ``total`` is the DISTINCT
        ``instance_id`` seen for the type, ``healthy`` is the DISTINCT
        ``instance_id`` with a healthy row, and ``unhealthy`` is the remainder. One
        process serving 22 worker_types owns 22 rows store-wide but is ONE replica
        of each of those types.

        NOTE: this reports what workers last SAID, not what is true. Nothing marks a
        silently-dead worker unhealthy yet — that is R4's liveness sweeper. Until R4,
        two kill -9'd replicas still count as healthy here, and that is correct for R2.
        """
        with self._lock:
            seen: dict[str, dict[str, set[str]]] = {}
            for w in self._store.values():
                if not w.worker_type:
                    continue
                row = seen.setdefault(w.worker_type, {"all": set(), "healthy": set()})
                row["all"].add(w.instance_id)
                if w.health_status in HEALTHY_STATUSES:
                    row["healthy"].add(w.instance_id)
            summary: dict[str, dict[str, int]] = {}
            for worker_type, sets in seen.items():
                total = len(sets["all"])
                healthy = len(sets["healthy"])
                summary[worker_type] = {
                    "total": total, "healthy": healthy, "unhealthy": total - healthy,
                }
            return summary

    def app_summary(self) -> dict[str, dict]:
        """``{worker_app: {nodes_total, nodes_healthy, replicas_total,
        replicas_healthy, all_synthesized}}`` — the read behind /workers/apps.

        The counting rule itself (nodes are REGISTRATIONS, replicas are
        PROCESSES, a synthesized instance_id excluded from the replica sets
        entirely) lives in ``app_instance_aggregation.app_summary`` — shared
        with ``HanaWorkerRegistryStore`` (SA-2047 Task 6) so the two stores
        cannot drift onto two different rules. This method's only job is the
        lock and handing over the rows.
        """
        with self._lock:
            return app_instance_aggregation.app_summary(self._store.values())

    def list_app_instances(self, worker_app: str) -> list[tuple[WorkerRegistration, list[str]]]:
        """One entry per PROCESS of an app: (freshest registration, node types).

        Folding logic lives in ``app_instance_aggregation.list_app_instances``
        (shared with ``HanaWorkerRegistryStore``, SA-2047 Task 6): rows are
        folded by instance_id and the freshest heartbeat — compared via
        ``parse_ts``, never a raw string — represents the process, the same
        rule ``get_by_worker_type`` uses and for the same reason: the other
        rows carry identical process facts.
        """
        if not worker_app:
            return []
        with self._lock:
            return app_instance_aggregation.list_app_instances(self._store.values(), worker_app)

    def list_all(self) -> list[WorkerRegistration]:
        with self._lock:
            return list(self._store.values())

    def sweep_stale(self, stale_after_seconds: int, remove_after_seconds: int) -> SweepResult:
        """Mark instances silent longer than stale_after unhealthy; remove those
        silent longer than remove_after. Returns counts + the worker_types this
        sweep drove to 0 healthy (the catalog.changed trigger).

        Age is via parse_ts -- NEVER a raw-string compare (the SA-2058
        cap-eviction defect). Blank/unparseable last_heartbeat parses to
        datetime.min = maximally stale. Runs entirely under the store lock.
        """
        now = self._now()
        stale_cutoff = now - timedelta(seconds=stale_after_seconds)
        remove_cutoff = now - timedelta(seconds=remove_after_seconds)
        result = SweepResult()
        with self._lock:
            to_mark: list[WorkerRegistration] = []
            to_remove: list[tuple[str, str]] = []   # (store key, worker_type)
            for w in self._store.values():
                hb = parse_ts(w.last_heartbeat)
                if hb < remove_cutoff:
                    to_remove.append((w.id, w.worker_type))
                elif hb < stale_cutoff and w.health_status in HEALTHY_STATUSES:
                    to_mark.append(w)

            # Transition detection must cover BOTH a fresh mark AND removal of a
            # still-healthy ancient row (a never-heartbeat worker that crossed
            # remove_after before any sweep marked it). Compute healthy counts over
            # every affected type BEFORE mutating, then after.
            affected = {w.worker_type for w in to_mark if w.worker_type}
            affected |= {wt for _, wt in to_remove if wt}
            before = {wt: self.count_healthy(wt) for wt in affected}

            for w in to_mark:
                w.health_status = "unhealthy"
            for key, _ in to_remove:
                self._store.pop(key, None)
            result.marked_count = len(to_mark)
            result.removed_count = len(to_remove)

            for wt in affected:
                if before[wt] > 0 and self.count_healthy(wt) == 0:
                    sv = next((w.spec_version for w in to_mark if w.worker_type == wt), "")
                    result.emptied.append((wt, sv))
        return result
