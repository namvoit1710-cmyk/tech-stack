"""Thread-safe in-memory IReadyTaskStore with the single-row state machine (SA-2027, B2).

The single lock IS the CAS: every transition (claim/renew/complete/sweep) does its
state check and mutation inside one critical section, so two Event-Mesh consumer
threads — or two competing lease pollers — can never both move the same row. This is
the RT#1 root fix expressed for a single process; a durable backing store (SPEC §6
graduation) swaps the dict + lock for a row-level `UPDATE … WHERE … RETURNING`, same
semantics. Durability stays in-memory (WCP re-drives on restart); the durable
leased-set marker is B8.

``_now()`` is a seam so tests can drive lease expiry without sleeping.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.layer2_application.interfaces.ready_task_store_interface import (
    IReadyTaskStore,
    TaskRow,
    TaskState,
    COMPLETE_OK,
    COMPLETE_SUPERSEDED,
    COMPLETE_UNKNOWN,
    ENQUEUE_OK,
    ENQUEUE_DUPLICATE,
    ENQUEUE_ADMISSION_FULL,
    SweepExpiredResult,
)


class MemoryReadyTaskStore(IReadyTaskStore):
    def __init__(
        self,
        max_bytes_global: int = 0,
        max_bytes_per_tenant: int = 0,
        max_bytes_per_partition: int = 0,
    ) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, TaskRow] = {}
        # B6 admission: byte quotas (0 = unlimited). Every quota is over the SAME
        # bytes each row occupies while it lives in the store — whether READY or
        # LEASED — so ready-depth + leased-in-flight are bounded by one accounting.
        self._max_bytes_global = max(0, int(max_bytes_global or 0))
        self._max_bytes_per_tenant = max(0, int(max_bytes_per_tenant or 0))
        self._max_bytes_per_partition = max(0, int(max_bytes_per_partition or 0))
        self._bytes_total = 0
        self._bytes_by_tenant: dict[str, int] = {}
        self._bytes_by_partition: dict[tuple[str, str], int] = {}
        self._row_bytes: dict[str, int] = {}   # task_id → bytes, for exact decrement
        # B8 restart-safe leased set: task_id → lease_generation for every LEASED row.
        # In-memory here (lost on restart, where WCP re-drives anyway); a durable store
        # persists this so restart can wait out the lease TTL before re-driving.
        self._leased_marker: dict[str, int] = {}

    # --- time seam (overridable in tests) ---
    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _now_iso(self) -> str:
        return self._now().isoformat()

    @staticmethod
    def _to_utc_iso(s: str) -> str:
        """Normalize a caller-supplied timestamp to UTC isoformat so the
        lexicographic `<`/`<=` comparisons below stay chronologically correct.
        All internal timestamps come from `_now()` (already UTC); this guards the
        intake boundary against a future caller passing a non-UTC offset."""
        if not s:
            return s
        try:
            return datetime.fromisoformat(s).astimezone(timezone.utc).isoformat()
        except ValueError:
            return s

    # Charged per JSON container ({ or [) to cover Python dict/list object overhead
    # that a raw json length under-counts (many tiny nested containers). Keeps the
    # accounted bytes within a small constant factor of real live memory.
    _CONTAINER_OVERHEAD = 64
    # Charged when a row's size can't be measured — fail CLOSED (an admission gate
    # must never admit an unmeasurable row for free when a quota is set).
    _UNMEASURABLE_BYTES = 1 << 31

    @classmethod
    def _estimate_bytes(cls, row: TaskRow) -> int:
        """Approximate the untrusted-write bytes a row holds (payload + node_config):
        the JSON length PLUS a per-container overhead (a JSON length alone
        under-counts many-small-nested-dict payloads vs their live Python memory).
        When the queue graduates to payload_ref pointers (SPEC §6), this drops to
        the pointer size and the SAME quota admits far more tasks; logic unchanged."""
        try:
            s = json.dumps(row.payload, default=str) + json.dumps(row.node_config, default=str)
        except Exception:
            return cls._UNMEASURABLE_BYTES   # fail closed
        return len(s) + (s.count("{") + s.count("[")) * cls._CONTAINER_OVERHEAD

    def _admit(self, tenant: str, partition: tuple[str, str], nbytes: int) -> bool:
        """True if adding *nbytes* keeps every level (partition ⊂ tenant ⊂ global)
        within quota. Caller holds the lock."""
        if self._max_bytes_global and self._bytes_total + nbytes > self._max_bytes_global:
            return False
        if self._max_bytes_per_tenant and self._bytes_by_tenant.get(tenant, 0) + nbytes > self._max_bytes_per_tenant:
            return False
        if self._max_bytes_per_partition and self._bytes_by_partition.get(partition, 0) + nbytes > self._max_bytes_per_partition:
            return False
        return True

    def _account_add(self, task_id: str, tenant: str, partition: tuple[str, str], nbytes: int) -> None:
        self._row_bytes[task_id] = nbytes
        self._bytes_total += nbytes
        self._bytes_by_tenant[tenant] = self._bytes_by_tenant.get(tenant, 0) + nbytes
        self._bytes_by_partition[partition] = self._bytes_by_partition.get(partition, 0) + nbytes

    def _account_remove(self, task_id: str, tenant: str, partition: tuple[str, str]) -> None:
        nbytes = self._row_bytes.pop(task_id, 0)
        self._bytes_total -= nbytes
        if self._bytes_by_tenant.get(tenant, 0) <= nbytes:
            self._bytes_by_tenant.pop(tenant, None)
        else:
            self._bytes_by_tenant[tenant] -= nbytes
        if self._bytes_by_partition.get(partition, 0) <= nbytes:
            self._bytes_by_partition.pop(partition, None)
        else:
            self._bytes_by_partition[partition] -= nbytes

    @staticmethod
    def _snapshot(row: TaskRow) -> TaskRow:
        """Return an isolated copy: `dataclasses.replace` is shallow, so also
        copy the mutable `payload`/`node_config` dicts. Consumers (the lease
        endpoint, callback path) mutate snapshots freely without touching stored
        state or another outstanding snapshot."""
        return replace(row, payload=dict(row.payload), node_config=dict(row.node_config))

    # --- intake (B1, admission B6) ---
    def enqueue(self, row: TaskRow) -> str:
        partition = (row.tenant, row.worker_type)
        nbytes = self._estimate_bytes(row)
        with self._lock:
            if row.task_id in self._rows:
                return ENQUEUE_DUPLICATE
            # Admission FIRST (atomic with the insert under this lock): reject a row
            # that would overflow any byte quota, so an untrusted flood can't grow
            # the shared queue without bound (muse #1 risk). The caller turns this
            # into Event-Mesh backpressure (redelivery), never a 429 to WCP.
            if not self._admit(row.tenant, partition, nbytes):
                return ENQUEUE_ADMISSION_FULL
            # Store a normalized, isolated copy — never the caller's object (so a
            # later mutation of the caller's row / its payload can't reach the
            # store, and an enqueue never lands half-set).
            enqueued_at = self._to_utc_iso(row.enqueued_at) or self._now_iso()
            stored = replace(
                row,
                enqueued_at=enqueued_at,
                visible_at=self._to_utc_iso(row.visible_at) or enqueued_at,
                state=TaskState.READY,
                lease_token=None,
                payload=dict(row.payload),
                node_config=dict(row.node_config),
            )
            self._rows[row.task_id] = stored
            self._account_add(row.task_id, row.tenant, partition, nbytes)
            return ENQUEUE_OK

    # --- transitions (B2), all under the lock = single-row CAS ---
    def claim(
        self, worker_type: str, tenant: str, max_n: int, lease_ttl_seconds: int,
        max_total_leased: int | None = None, instance_id: str = "",
    ) -> list[TaskRow]:
        if max_n <= 0:
            return []
        now = self._now()
        now_iso = now.isoformat()
        deadline_iso = (now + timedelta(seconds=lease_ttl_seconds)).isoformat()
        leased: list[TaskRow] = []
        with self._lock:
            # Hard total-leased ceiling (B4): bound in-flight LEASED rows across ALL
            # partitions so a flood can't grow the leased set without limit. B6
            # extends this to a byte-quota hierarchy; here it's a simple count cap.
            # <=0 (or None) = unlimited — a configured 0 must NOT brick leasing.
            # Count only LIVE leases (deadline not yet passed): an expired-but-unswept
            # lease is reclaimable, so it must not permanently consume a slot and
            # wedge all leasing before the B8 sweeper reaps it.
            headroom = max_n
            if max_total_leased is not None and max_total_leased > 0:
                current_leased = sum(
                    1 for r in self._rows.values()
                    if r.state == TaskState.LEASED and (not r.deadline or r.deadline >= now_iso)
                )
                headroom = min(max_n, max(0, max_total_leased - current_leased))
                if headroom <= 0:
                    return []
            # Leasable rows in this partition, FIFO by enqueued_at. SA-2424: a row
            # pinned to a specific instance (target_instance_id set) is only
            # claimable by that EXACT instance; unpinned rows by any. Exact match
            # (not host fallback) keeps two same-host replicas deterministic.
            candidates = [
                r for r in self._rows.values()
                if r.worker_type == worker_type
                and r.tenant == tenant
                and r.state == TaskState.READY
                and (not r.visible_at or r.visible_at <= now_iso)
                and (not getattr(r, "target_instance_id", "")
                     or r.target_instance_id == instance_id)
            ]
            candidates.sort(key=lambda r: r.enqueued_at)
            for r in candidates[:headroom]:
                r.state = TaskState.LEASED
                r.lease_token = secrets.token_urlsafe(16)  # 128-bit, fresh per lease
                r.lease_generation += 1
                r.deadline = deadline_iso
                r.last_lease_at = now_iso
                self._leased_marker[r.task_id] = r.lease_generation   # restart-safe set (B8)
                leased.append(self._snapshot(r))  # isolated snapshot for the leaser
        return leased

    @staticmethod
    def _token_matches(stored: str | None, presented: str | None) -> bool:
        # Self-sufficient: an empty/missing token on EITHER side never matches, so
        # the token leg doesn't rely on the state check ordering to block an
        # `X-Lease-Token: ""` against a READY/swept row (lease_token=None). Then a
        # constant-time compare — no timing oracle on the lease token (SPEC §5).
        if not stored or not presented:
            return False
        return hmac.compare_digest(stored, presented)

    def renew(self, task_id: str, lease_token: str, lease_ttl_seconds: int) -> str | None:
        """CAS extend deadline WHERE task_id AND lease_token AND state==LEASED.
        Returns the NEW absolute deadline (ISO) on success, or None (⇒
        TASK_SUPERSEDED) if 0 rows match — the caller renews against this returned
        deadline, never its own clock."""
        now = self._now()
        deadline_iso = (now + timedelta(seconds=lease_ttl_seconds)).isoformat()
        with self._lock:
            r = self._rows.get(task_id)
            if r is None or r.state != TaskState.LEASED or not self._token_matches(r.lease_token, lease_token):
                return None
            r.deadline = deadline_iso
            r.last_lease_at = now.isoformat()
            return deadline_iso

    def count_leased(self) -> int:
        with self._lock:
            return sum(1 for r in self._rows.values() if r.state == TaskState.LEASED)

    def complete(self, task_id: str, lease_token: str,
                 tenant: str | None = None, worker_type: str | None = None) -> str:
        """CAS complete WHERE task_id AND lease_token AND state==LEASED, with
        TRIPLE-BINDING (SPEC §5): when *tenant*/*worker_type* are supplied they must
        ALSO equal the STORED row's — all three checks fused under one lock, all
        resolved from the row (never the request body). A pull credential scoped to
        one (tenant, worker_type) can never complete another's task, and — because a
        push task lives only in the pending store, never here — can never complete a
        push task (CROSS_MODE_CALLBACK_FORGERY). Token compare is constant-time."""
        with self._lock:
            r = self._rows.get(task_id)
            if r is None:
                return COMPLETE_UNKNOWN
            if (r.state != TaskState.LEASED
                    or not self._token_matches(r.lease_token, lease_token)
                    or (tenant is not None and r.tenant != tenant)
                    or (worker_type is not None and r.worker_type != worker_type)):
                return COMPLETE_SUPERSEDED
            # CAS matched: terminal. Remove the row (at-most-once completion) — the
            # removal is CONDITIONAL on the match above, not a blind remove(); a
            # second callback finds no row → COMPLETE_UNKNOWN, i.e. rejected.
            # NOTE (B7 fence): once the row is gone, enqueue's presence-based dedup
            # no longer remembers it, so an at-least-once REDELIVERY of the same
            # dispatch could re-enqueue+re-execute this task. Store-level
            # at-most-once holds only while the row exists; the stable
            # idempotency-key fence (B7) MUST cover the already-DONE/purged case.
            r.state = TaskState.DONE
            del self._rows[task_id]
            self._account_remove(task_id, r.tenant, (r.tenant, r.worker_type))
            self._leased_marker.pop(task_id, None)
            return COMPLETE_OK

    def sweep_expired_leases(
        self, max_attempts: int = 0, backoff_base_seconds: float = 0,
    ) -> SweepExpiredResult:
        now = self._now()
        now_iso = now.isoformat()
        re_enqueued: list[TaskRow] = []
        poisoned: list[TaskRow] = []
        with self._lock:
            for task_id, r in list(self._rows.items()):
                if not (r.state == TaskState.LEASED and r.deadline and r.deadline < now_iso):
                    continue
                next_attempt = r.attempt_count + 1
                if max_attempts and next_attempt >= max_attempts:
                    # Poison: exhausted its lease attempts → terminal, remove (B8).
                    r.attempt_count = next_attempt
                    poisoned.append(self._snapshot(r))
                    del self._rows[task_id]
                    self._account_remove(task_id, r.tenant, (r.tenant, r.worker_type))
                    self._leased_marker.pop(task_id, None)
                    continue
                # Re-enqueue with exponential backoff on visibility.
                r.state = TaskState.READY
                r.lease_token = None
                r.attempt_count = next_attempt
                # Bump lease_generation so the task.lease_expired event carries a
                # strictly-higher ordering token than the lease it expires (and the
                # next claim bumps it again) → WCP can resequence lease_expired
                # before its re-lease under Event-Mesh reordering.
                r.lease_generation += 1
                backoff = 0.0
                if backoff_base_seconds:
                    # Cap the EXPONENT before the value cap so an unbounded-retry
                    # config can't build a 300-digit int each sweep.
                    backoff = min(backoff_base_seconds * (2 ** min(next_attempt - 1, 20)), 3600.0)
                # Reset enqueued_at to NOW: it measures the READY-wait, so a task
                # re-enqueued after a lease expiry is NOT immediately reaped by
                # sweep_stale_ready as WORKER_UNAVAILABLE (which would false-kill an
                # actively-retrying task and double-emit with task.lease_expired).
                # Its max-ready clock restarts from this re-enqueue.
                r.enqueued_at = now_iso
                r.visible_at = (now + timedelta(seconds=backoff)).isoformat()
                r.deadline = ""
                self._leased_marker.pop(task_id, None)   # no longer leased
                re_enqueued.append(self._snapshot(r))
        return SweepExpiredResult(re_enqueued, poisoned)

    def leased_set_snapshot(self) -> dict:
        with self._lock:
            return dict(self._leased_marker)

    def sweep_stale_ready(self, max_ready_seconds: int) -> list[TaskRow]:
        cutoff_iso = (self._now() - timedelta(seconds=max_ready_seconds)).isoformat()
        stale: list[TaskRow] = []
        with self._lock:
            for task_id, r in list(self._rows.items()):
                # Measure the leasable-wait from visible_at, NOT enqueued_at: a row in
                # inter-attempt backoff (future visible_at) is intentionally invisible
                # and must NOT be reaped mid-backoff (else a large backoff ≥ max-ready
                # would false-kill an actively-retrying task as WORKER_UNAVAILABLE).
                ref = r.visible_at or r.enqueued_at
                if r.state == TaskState.READY and ref and ref < cutoff_iso:
                    stale.append(self._snapshot(r))
                    del self._rows[task_id]  # terminal → caller reports WORKER_UNAVAILABLE (B8)
                    self._account_remove(task_id, r.tenant, (r.tenant, r.worker_type))
        return stale

    def purge_by_run(self, run_id: str) -> list[TaskRow]:
        """Remove ALL rows for *run_id* (WCP cancel/reset — B7). Returns the purged
        rows so the caller can emit terminal lifecycle events. Frees their bytes. A
        stale-run task, once purged, is neither leaseable nor completable (a late
        callback finds no row → UNKNOWN → rejected), which is how STALE_RUN_TASK_LEASED
        is prevented alongside the run_generation echoed on every result event."""
        purged: list[TaskRow] = []
        with self._lock:
            for task_id, r in list(self._rows.items()):
                if r.run_id == run_id:
                    purged.append(self._snapshot(r))
                    del self._rows[task_id]
                    self._account_remove(task_id, r.tenant, (r.tenant, r.worker_type))
                    self._leased_marker.pop(task_id, None)
        return purged

    # --- reads ---
    def get(self, task_id: str) -> Optional[TaskRow]:
        with self._lock:
            r = self._rows.get(task_id)
            return self._snapshot(r) if r is not None else None

    def count(self) -> int:
        with self._lock:
            return len(self._rows)

    # --- observability (B9) ---
    @staticmethod
    def _stat_row(worker_type: str, tenant: str, ready: int, leased: int,
                  oldest_age: float, nbytes: int) -> dict:
        return {
            "worker_type": worker_type,
            "tenant": tenant,
            # admitted_depth = LEASABLE backlog (READY AND visible now): the KEDA
            # scale-from-zero signal. A backed-off/not-yet-visible row is NOT counted
            # — no worker could lease it, so it must not inflate the scale signal.
            "admitted_depth": ready,
            "leased": leased,
            "oldest_ready_age_seconds": round(oldest_age, 3),
            "bytes": nbytes,
        }

    def _partition_stats_locked(self, worker_type: str, tenant: str, now: datetime) -> dict:
        now_iso = now.isoformat()
        ready = 0
        leased = 0
        oldest_ready_enq = None
        for r in self._rows.values():
            if r.worker_type != worker_type or r.tenant != tenant:
                continue
            if r.state == TaskState.READY and (not r.visible_at or r.visible_at <= now_iso):
                ready += 1
                if r.enqueued_at and (oldest_ready_enq is None or r.enqueued_at < oldest_ready_enq):
                    oldest_ready_enq = r.enqueued_at
            elif r.state == TaskState.LEASED:
                leased += 1
        oldest_age = self._age_seconds(oldest_ready_enq, now)
        return self._stat_row(worker_type, tenant, ready, leased, oldest_age,
                              self._bytes_by_partition.get((tenant, worker_type), 0))

    @staticmethod
    def _age_seconds(iso: Optional[str], now: datetime) -> float:
        if not iso:
            return 0.0
        try:
            return max(0.0, (now - datetime.fromisoformat(iso)).total_seconds())
        except ValueError:
            return 0.0

    def queue_stats(self, worker_type: str, tenant: str = "") -> dict:
        """Point-in-time stats for one (tenant, worker_type) partition (B9)."""
        now = self._now()
        with self._lock:
            return self._partition_stats_locked(worker_type, tenant, now)

    def stats_all(self) -> list[dict]:
        """Per-partition stats for every partition with rows — SINGLE pass O(N), not
        O(partitions × rows) (the periodic-sweep refresh runs under the store lock)."""
        now = self._now()
        now_iso = now.isoformat()
        agg: dict[tuple[str, str], list] = {}   # (tenant,wt) → [ready, leased, oldest_enq]
        with self._lock:
            for r in self._rows.values():
                key = (r.tenant, r.worker_type)
                a = agg.get(key)
                if a is None:
                    a = [0, 0, None]
                    agg[key] = a
                if r.state == TaskState.READY and (not r.visible_at or r.visible_at <= now_iso):
                    a[0] += 1
                    if r.enqueued_at and (a[2] is None or r.enqueued_at < a[2]):
                        a[2] = r.enqueued_at
                elif r.state == TaskState.LEASED:
                    a[1] += 1
            return [
                self._stat_row(wt, t, a[0], a[1], self._age_seconds(a[2], now),
                               self._bytes_by_partition.get((t, wt), 0))
                for (t, wt), a in agg.items()
            ]
