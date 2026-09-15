"""The pull-delivery task store — ONE store, ONE row, a state machine (SA-2027, B2).

RT#1 root fix: a pull task is a single row with a state machine (READY→LEASED→DONE).
Lease / renew / complete / sweep are all **single-row CAS** that fuse (state check +
mutation) — never a two-store move, never a blind remove(). In this single-process
in-memory implementation the CAS is "under one lock"; the interface is written so the
A→B durable swap (SPEC §6 graduation) is a storage swap, not a redesign.

This is the pull dual of ``IPendingTaskStore`` (dispatched push tasks awaiting a
callback). Do NOT fold pull state into the pending store — they are different
lifecycles. B1 shipped enqueue + get + count; B2 adds the transitions below. The
endpoints that call them arrive later: lease/renew (B3/B4), complete (B5, from the
hardened callback), the sweeper forks (B8).
"""

from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, Optional, Any

# Result of sweeping expired leases (B8): rows re-enqueued (attempt++, backoff) vs
# rows that exhausted max_lease_attempts and were poisoned (terminal → DLQ).
SweepExpiredResult = namedtuple("SweepExpiredResult", ["re_enqueued", "poisoned"])


class TaskState(str, Enum):
    READY = "READY"    # enqueued, awaiting a lease
    LEASED = "LEASED"  # claimed by a worker, lease TTL running
    DONE = "DONE"      # completed (terminal)


# Result codes for the complete() CAS (B5 maps these to WCP outcomes).
COMPLETE_OK = "COMPLETED"          # the lease matched — task moved LEASED→DONE
COMPLETE_SUPERSEDED = "SUPERSEDED"  # row exists but lease/state didn't match (late/forged)
COMPLETE_UNKNOWN = "UNKNOWN"       # no such row (already purged / never existed)

# Result codes for enqueue() (B6 admission).
ENQUEUE_OK = "OK"                  # newly stored
ENQUEUE_DUPLICATE = "DUPLICATE"    # task_id already present (at-least-once redelivery)
ENQUEUE_ADMISSION_FULL = "ADMISSION_FULL"  # a byte quota (partition/tenant/global) would overflow


@dataclass
class TaskRow:
    """A single pull task and its lease state (SPEC §3).

    ``payload`` is inline for B1/B2; SPEC §6 swaps it for a ``payload_ref`` file_id
    pointer (admission counts BYTES, not inline blobs) — that lands with admission
    (B6). ``tenant`` is the (tenant, worker_type) partition key — plumbed for real
    in B3/B5; blank until then is the single un-partitioned pull namespace.
    ``run_generation`` (from WCP dispatch) is checked in the lease/complete CAS in B7.
    """
    task_id: str
    run_id: str
    node_id: str
    worker_type: str
    spec_version: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    node_config: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    tenant: str = ""
    delivery_mode: str = "pull"
    # SA-2424: per-node/whole-flow instance pin carried from WCP dispatch. When
    # target_instance_id is set, only the matching instance may lease this row
    # (claim() filters on it) — the pull dual of the push dispatcher's routing.
    # target_host is retained for parity with the push path; blank = no pin (any).
    target_instance_id: str = ""
    target_host: str = ""
    # --- state machine (B2) ---
    state: TaskState = TaskState.READY
    enqueued_at: str = ""          # ISO 8601
    visible_at: str = ""           # ISO 8601; row is leasable when visible_at <= now
    deadline: str = ""             # ISO 8601 absolute (executor clock); lease expiry
    lease_token: Optional[str] = None   # fresh random per lease; None when READY
    lease_generation: int = 0      # monotonic; +1 on every claim
    run_generation: str = ""       # from WCP dispatch (B7 supersession)
    attempt_count: int = 0         # +1 each time a lease expires and re-enqueues
    last_lease_at: str = ""        # ISO 8601


class IReadyTaskStore(Protocol):
    # --- intake (B1, admission B6) ---
    def enqueue(self, row: TaskRow) -> str:
        """Atomic admission + store. Returns one of ENQUEUE_OK (newly stored),
        ENQUEUE_DUPLICATE (task_id already present — at-least-once redelivery), or
        ENQUEUE_ADMISSION_FULL (a byte quota — (tenant,type) ⊂ tenant ⊂ global —
        would overflow; the caller applies Event-Mesh backpressure, never a 429).
        Check + admission + insert are one atomic step."""
        ...

    # --- transitions (B2), all single-row/batch CAS ---
    def claim(
        self, worker_type: str, tenant: str, max_n: int, lease_ttl_seconds: int,
        max_total_leased: int | None = None, instance_id: str = "",
    ) -> list[TaskRow]:
        """Atomically move up to *max_n* leasable rows from the caller's
        (tenant, worker_type) partition READY→LEASED (FIFO by enqueued_at). Each
        gets a fresh lease_token, lease_generation+1, deadline=now+ttl. Returns
        snapshots (copies) of the leased rows. Empty list if none available.
        *max_total_leased* (B4) caps in-flight LEASED rows across all partitions.
        *instance_id* (SA-2424) is the leasing worker instance: a row pinned to a
        specific instance (target_instance_id set) is only claimable by that exact
        instance; unpinned rows are claimable by any."""
        ...

    def renew(self, task_id: str, lease_token: str, lease_ttl_seconds: int) -> "str | None":
        """CAS extend deadline WHERE task_id AND lease_token AND state==LEASED.
        Returns the new absolute deadline (ISO) on success, or None (⇒
        TASK_SUPERSEDED) if 0 rows match."""
        ...

    def count_leased(self) -> int: ...

    def complete(self, task_id: str, lease_token: str,
                 tenant: "str | None" = None, worker_type: "str | None" = None) -> str:
        """CAS complete WHERE task_id AND lease_token AND state==LEASED → DONE, with
        optional triple-binding on tenant + worker_type (all from the stored row,
        constant-time token compare — SPEC §5). Returns COMPLETE_OK /
        COMPLETE_SUPERSEDED / COMPLETE_UNKNOWN. Never a blind remove(): a
        non-matching lease/tenant/type or wrong state is rejected, not applied."""
        ...

    def sweep_expired_leases(
        self, max_attempts: int = 0, backoff_base_seconds: float = 0,
    ) -> SweepExpiredResult:
        """LEASED rows whose deadline < now: re-enqueue → READY (lease_token=None,
        attempt_count+1, visible_at = now + exponential backoff). A row whose next
        attempt would reach *max_attempts* is POISONED instead (removed, terminal).
        Returns SweepExpiredResult(re_enqueued, poisoned). max_attempts/backoff 0 =
        off (unbounded retries, no backoff)."""
        ...

    def leased_set_snapshot(self) -> dict:
        """{task_id: lease_generation} for every currently-LEASED row (B8). The
        restart-safe leased-set: on restart the executor waits out the lease TTL for
        these before re-driving, so a still-alive worker's lease is honoured."""
        ...

    def sweep_stale_ready(self, max_ready_seconds: int) -> list[TaskRow]:
        """READY rows enqueued longer than max_ready_seconds ago (no worker ever
        leased them) → removed and returned so the caller can terminate them as
        WORKER_UNAVAILABLE (B8). Terminal; the row leaves the store."""
        ...

    def purge_by_run(self, run_id: str) -> list[TaskRow]:
        """Remove ALL rows for a run (WCP cancel/reset — B7). Returns the purged
        rows. Frees their bytes."""
        ...

    # --- reads ---
    def get(self, task_id: str) -> Optional[TaskRow]: ...
    def count(self) -> int: ...

    # --- observability (B9) ---
    def queue_stats(self, worker_type: str, tenant: str = "") -> dict:
        """Point-in-time stats for one (tenant, worker_type): admitted_depth (the
        leasable backlog = KEDA scale-from-zero signal), leased, oldest_ready_age,
        bytes."""
        ...

    def stats_all(self) -> list[dict]:
        """queue_stats for every partition that currently has rows."""
        ...
