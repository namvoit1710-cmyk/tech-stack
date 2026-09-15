"""Projector snapshot store — Phase 4 foundation.

Persists periodic ``ProjectedRunState`` materialisations keyed by
``(run_id, sequence)`` so cold-cache replay becomes O(Δ) events instead
of O(|total log|) per run.

Correctness is unchanged by this module: the event log remains the
source of truth. A missing or stale snapshot falls back to full replay
from the log — snapshots are a pure performance optimisation.

Schema versioning: ``Snapshot.schema_version`` is compared against the
current reducer's ``SCHEMA_VERSION`` constant on load. Below-threshold
snapshots are ignored so code changes to the reducer are self-healing
— old snapshots fall back to full replay, new ones are written on the
next threshold crossing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Snapshot:
    """One materialised ``ProjectedRunState`` at a specific event sequence.

    ``state_json`` is the opaque serialised state — the codec owns its
    shape. ``sequence`` is the last ``EventLogEnvelope.sequence``
    folded into ``state_json``; the replay tail is the set of envelopes
    with ``sequence > snapshot.sequence``.
    """

    run_id: str
    sequence: int
    schema_version: int
    state_json: str
    created_at: str


class ISnapshotRepository(Protocol):
    """Per-run snapshot store with schema-version filtering."""

    def save(self, snapshot: Snapshot) -> None:
        """Persist ``snapshot``.

        Idempotent on ``(run_id, sequence)`` — re-saving the same key
        is a no-op or an in-place refresh of ``created_at``; callers
        should not rely on either behaviour.
        """

    def load_latest(
        self, run_id: str, *, min_schema_version: int,
    ) -> Snapshot | None:
        """Return the highest-``sequence`` snapshot for ``run_id`` whose
        ``schema_version >= min_schema_version``.

        Returns ``None`` if the run has no snapshots or all snapshots
        fall below the schema threshold. Stale snapshots are not
        deleted by this call — retention is the caller's concern via
        ``delete_older_than``.
        """

    def delete_older_than(self, run_id: str, keep_sequence: int) -> int:
        """Remove every snapshot for ``run_id`` with ``sequence < keep_sequence``.

        Returns the number of snapshots removed. The retention policy
        (how many to keep) lives in the caller — this method is a
        bounded-scan primitive.
        """
