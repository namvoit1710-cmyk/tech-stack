"""Deterministic identifier value object.

``EventId`` owns the rules for deriving **stable, replay-safe ids** — for domain
events, child-workflow runs, and outbox entries. Same inputs always produce the
same id (uuid5 over a project-private namespace, or a sha256 digest), so a
redelivered or replayed action reuses the existing id and the event log / outbox
dedupe on ``(run_id, event_id)`` instead of double-applying.

It is a value-object namespace: the derivation rules live here as classmethods;
the ids themselves ride the wire as plain strings.
"""

from __future__ import annotations

import hashlib
import uuid


class EventId:
    """Deterministic id derivation (uuid5 / sha256). Not instantiated."""

    # Project-private uuid5 namespaces. Distinct so the two id spaces can never
    # collide. **Must remain stable** — changing either re-derives different ids
    # for in-flight events / nested runs.
    _ORCHESTRATION_EVENT_NAMESPACE = uuid.UUID("8b5f1e6e-6b1b-5a21-9cbe-0e4a9f4c9a01")
    _CHILD_RUN_NAMESPACE = uuid.UUID("9c2a7d44-3e1b-4f88-a6d2-1b7e4c0f5a93")

    @classmethod
    def derive(cls, causation_id: str, correlation_key: str) -> str:
        """Stable event id from ``(causation_id, correlation_key)``.

        ``causation_id`` is the id of the upstream event that caused this one;
        ``correlation_key`` uniquely identifies *what* this new event represents
        within its cause (e.g. ``f"edge:{wire_id}:{target_node_id}"`` for
        dispatcher fan-out, ``f"loop:{loop_node_id}:iter:{index}"`` for loop
        iterations). Same inputs → same uuid5, so redeliveries dedupe at the log.
        """
        name = f"{causation_id}|{correlation_key}"
        return str(uuid.uuid5(cls._ORCHESTRATION_EVENT_NAMESPACE, name))

    @classmethod
    def derive_child_run(
        cls, parent_run_id: str, node_id: str, generation: int, visit: int = 0,
    ) -> str:
        """Bounded, deterministic run id for a WORKFLOW node's child run.

        A uuid5 keeps the id at a fixed 36 chars at any nesting depth (the legacy
        recursive ``f"{parent}_{node}_{gen}"`` format overflowed the event-log
        ``RUN_ID`` column past ~5 levels). Determinism is preserved so a
        redelivered/replayed WORKFLOW-node dispatch reuses the existing child run.
        ``generation`` bumps on reset/re-execute; ``visit`` is the SA-846 revisit
        counter so a cycle re-entry spawns a fresh child run instead of colliding
        with visit 0's row (visit 0 keeps the pre-visit name shape).
        """
        name = f"{parent_run_id}_{node_id}_{generation}"
        if visit:
            name += f"_v{visit}"
        return str(uuid.uuid5(cls._CHILD_RUN_NAMESPACE, name))

    @staticmethod
    def derive_outbox(run_id: str, task_id: str, op_index: int, kind: str) -> str:
        """Stable id for an outbox entry — survives handler replay.

        Redelivery of the producing event re-runs the handler, which re-derives
        the same id from ``(run_id, task_id, op_index, kind)``; the event log's
        ``(run_id, event_id)`` dedupe folds the second append to a no-op. For HITL
        resume_run (no ``task_id``) callers pass a stable seed (``"resume_run"``)
        in place of ``task_id``. Truncated to 16 hex chars — collision
        probability is negligible and keeps log payloads compact.
        """
        seed = f"{run_id}:{task_id}:{op_index}:{kind}".encode("utf-8")
        return hashlib.sha256(seed).hexdigest()[:16]
