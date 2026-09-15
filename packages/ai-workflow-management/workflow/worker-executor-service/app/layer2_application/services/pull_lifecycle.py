"""Pull-delivery WCP contract: lifecycle-event shapes + output-pointer validation (SA-2032, B7).

WCP is transparent to push-vs-pull dispatch, but a pull task's life has states a
pushed task doesn't — it can sit QUEUED (waiting to be leased, possibly by a
scaled-to-zero worker) and LEASED (a worker is running it). These lifecycle events
carry those states to WCP so:
  - a QUEUED wait is shown in the timeline and does NOT trip the misrouted-builtin
    dispatch-liveness sweep (a scale-to-zero queue-wait looks identical to that
    silent hang) — bounded instead by the ready-store max-ready-TTL;
  - every event echoes the STABLE idempotency_key + run_generation, so WCP dedups a
    re-driven task and rejects a stale-generation result.

The builders below produce the message dicts; the caller publishes them via
result_publisher (the event-mesh path back to WCP). Centralized here so the contract
is one source of truth across enqueue (B7), lease (B7), and the sweeper (B8).
"""

from __future__ import annotations

from typing import Any


# WCP-facing lifecycle message types.
EVT_ENQUEUED = "task.enqueued"          # → node QUEUED
EVT_LEASED = "task.leased"              # → node LEASED
EVT_LEASE_EXPIRED = "task.lease_expired"  # → re-queued (attempt++)
EVT_DLQ = "task.dlq"                    # poison task → dead-letter / alert channel
WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"    # distinct non-retryable terminal
TASK_FAILED_POISON = "TASK_FAILED_POISON"    # exhausted max_lease_attempts


def _base(row: Any) -> dict:
    return {
        "task_id": getattr(row, "task_id", ""),
        "run_id": getattr(row, "run_id", ""),
        "node_id": getattr(row, "node_id", ""),
        "worker_type": getattr(row, "worker_type", ""),
        "tenant": getattr(row, "tenant", ""),
        # The stable fence WCP dedups / generation-checks on.
        "idempotency_key": getattr(row, "idempotency_key", ""),
        "run_generation": getattr(row, "run_generation", ""),
        # Monotonic-per-task ORDERING token: strictly increases across
        # enqueued(0) → leased(1) → lease_expired(2) → re-leased(3) → …, so WCP can
        # last-writer-wins / resequence these events under Event-Mesh reordering
        # (they're emitted from different threads to one broker). Without it a
        # `leased` arriving before its preceding `lease_expired` would wedge the node.
        "lease_generation": getattr(row, "lease_generation", 0),
    }


def enqueued_event(row: Any) -> dict:
    p = _base(row)
    p["status"] = "QUEUED"
    return {"message_type": EVT_ENQUEUED, "correlation_id": p["run_id"], "payload": p}


def leased_event(row: Any) -> dict:
    p = _base(row)
    p["status"] = "LEASED"
    p["attempt_count"] = getattr(row, "attempt_count", 0)
    return {"message_type": EVT_LEASED, "correlation_id": p["run_id"], "payload": p}


def lease_expired_event(row: Any) -> dict:
    p = _base(row)
    p["status"] = "QUEUED"   # back to waiting
    p["attempt_count"] = getattr(row, "attempt_count", 0)
    return {"message_type": EVT_LEASE_EXPIRED, "correlation_id": p["run_id"], "payload": p}


def worker_unavailable_event(row: Any) -> dict:
    """A READY task that no worker ever leased within max-ready-TTL — a distinct,
    NON-RETRYABLE terminal (never re-driven into a void) + an alert signal (B8)."""
    p = _base(row)
    p["status"] = "FAILED"
    p["error"] = {
        "code": WORKER_UNAVAILABLE,
        "message": "no worker leased the task before max-ready-TTL",
        "retryable": False,
    }
    return {"message_type": "task.failed", "correlation_id": p["run_id"], "payload": p}


def poison_event(row: Any) -> dict:
    """A task that exhausted max_lease_attempts (re-leased and expired too many
    times) → a distinct NON-RETRYABLE terminal (never re-driven) — B8."""
    p = _base(row)
    p["status"] = "FAILED"
    p["attempt_count"] = getattr(row, "attempt_count", 0)
    p["error"] = {
        "code": TASK_FAILED_POISON,
        "message": "task exhausted its lease attempts (poison)",
        "retryable": False,
    }
    return {"message_type": "task.failed", "correlation_id": p["run_id"], "payload": p}


def dlq_event(row: Any) -> dict:
    """Dead-letter + alert signal for a poisoned task (B8). Routed to the DLQ/alert
    channel (topic binding is a broker/ops concern; the event is the contract)."""
    p = _base(row)
    p["attempt_count"] = getattr(row, "attempt_count", 0)
    return {"message_type": EVT_DLQ, "correlation_id": p["run_id"], "payload": p}


def output_pointer_ok(tenant: str, output_reference: str) -> bool:
    """UNTRUSTED_OUTPUT_POINTER guard (B7): a pull worker's result may name a file_id
    it does not own. The load-bearing enforcement is the tenant-scoped file-service
    refusing a cross-tenant read; the executor ALSO rejects a pointer that encodes a
    different tenant than the task's, by the ``tenants/<t>/`` path convention. No
    encoded tenant / no reference → allowed (the file-service still gates the read).

    Hardened (B7 red-team): case-insensitive marker; EVERY ``tenants/<t>/`` segment is
    checked (not just the first); a ``..`` inside a tenant-scoped ref is rejected as
    path traversal. The fragile ``<t>:`` prefix heuristic was dropped (it false-rejected
    legit ``host:port/...`` refs)."""
    if not output_reference or not tenant:
        return True
    ref = output_reference
    low = ref.casefold()
    marker = "tenants/"
    if marker in low and ".." in ref:
        return False   # traversal within a tenant-scoped path
    start = 0
    while True:
        idx = low.find(marker, start)
        if idx == -1:
            return True
        seg_start = idx + len(marker)
        encoded = ref[seg_start:].split("/", 1)[0].strip()
        if encoded and encoded != tenant:
            return False
        start = seg_start


def outputs_ok(tenant: str, outputs: Any) -> bool:
    """Recursively check every string leaf of a (possibly nested) outputs structure
    for a cross-tenant encoded pointer — a poisoned file_id can hide under any key,
    not just ``output_reference``/``uri`` (cf. SA-1949 nested file_ref)."""
    if not tenant:
        return True
    stack = [outputs]
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            if not output_pointer_ok(tenant, v):
                return False
        elif isinstance(v, dict):
            stack.extend(v.values())
        elif isinstance(v, (list, tuple)):
            stack.extend(v)
    return True
