"""Worker-registry observability MIRROR repository port — SA-2050 (R3).

A HANA-backed, human-queryable mirror of the executor's in-memory worker
registry (``AIW_WORKER_REGISTRY_MIRROR``, migration ``051``). The CP background
loop snapshot-reconciles ``IWorkerRegistryClient.list_workers()`` into this table
so an operator can see the live replicas in DBeaver / SQL. READ-ONLY for humans —
nothing reads it for dispatch or the palette (spec §1).

Backend: ``HanaWorkerRegistryMirrorRepository`` (SAP BTP / local HANA). In mock
mode (no HANA) the repo is never constructed and the loop no-ops (spec §7).
"""

from __future__ import annotations

from typing import NamedTuple, Protocol


class ReconcileResult(NamedTuple):
    """Outcome of one snapshot-reconcile pass."""

    upserted: int
    deleted: int


class IWorkerRegistryMirrorRepository(Protocol):
    """Port for the worker-registry observability mirror."""

    def reconcile(self, rows: list[dict]) -> ReconcileResult:
        """Snapshot-reconcile ``rows`` (from ``list_workers()``) into the mirror.

        Upsert each present row keyed by ``id`` (set ``LAST_SEEN_AT`` = now,
        ``FIRST_SEEN_AT`` only on first insert); DELETE rows whose ``id`` is NOT
        in ``rows`` (P1 current-state). An empty ``rows`` is a no-op — it never
        upserts and never deletes (the empty-snapshot guard also lives in the
        caller; spec §6). Returns ``(upserted, deleted)``.
        """
        ...

    def get_all(self) -> list[dict]:
        """Return every mirror row as a dict (tests / manual use — not an API)."""
        ...
