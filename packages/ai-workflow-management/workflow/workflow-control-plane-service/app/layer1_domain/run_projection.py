"""Run-state projection — the ``ProjectedRunState`` value object (``evolve``).

``ProjectedRunState`` is the structural state of a run rebuilt from its event
log. It is a rich value object: it folds events into itself (``apply`` — the
pure ``evolve`` reducer), serialises itself (``to_json`` / ``from_json``), and
compares itself to another projection (``diff`` — shadow parity). The
repo-driven replay (``rebuild_run_state``) is the thin application shell around
``apply`` and lives in ``layer2_application/projection/``.

--------------------------------------------------------------------
Partial rebuild — what is / isn't rebuildable from events today
--------------------------------------------------------------------

**Rebuildable (``apply``):**
- ``run_id``, ``workflow_id``, ``status``, ``started_at``, ``completed_at``,
  ``error``, ``output_data``, ``input_data`` — from the unified
  ``RunStatusChanged`` event (discriminated by ``status``)
- ``completed_nodes`` — from ``NodeCompleted`` + ``NodeFailed`` +
  ``NodeCancelled`` (non-skip reasons)
- ``skipped_nodes`` — from ``NodeCancelled`` (skip reasons)
- ``child_runs`` — from ``ChildRunStarted``
- ``variables`` — from ``NodeOutputProduced`` (Phase 5.1)

**NOT rebuildable today (Phase 5.2+ closes these):**
- ``topology`` — built by ``GraphTopology.from_workflow`` from the ``Workflow``
  entity; events carry edge/wire info but not the preparer output.

All reducer branches are **idempotent** — applying the same envelope twice
produces the same state (set-union for node sets; last-write for scalars). This
matches the Phase 4b.1 log-level dedupe (dedupe happens at ``append``, not
``apply``).

``SCHEMA_VERSION`` is the guard against reducer changes. Bump it when ``apply``
starts producing semantically different state for the same events; snapshots
below the new version are ignored by the snapshot store, which falls back to
full replay until the next threshold crossing writes a new-version snapshot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

from app.layer1_domain.event_log import EventLogEnvelope


# Fields the shadow-parity ``diff`` compares (snapshot+tail vs full replay).
_TRACKED_FIELDS = (
    "run_id",
    "workflow_id",
    "status",
    "started_at",
    "completed_at",
    "error",
    "output_data",
    "completed_nodes",
    "skipped_nodes",
    "child_runs",
    "variables",
    "input_data",
    "pending_timers",
)


@dataclass(frozen=True)
class ProjectedRunState:
    """Lifecycle + variables state rebuilt from the event log."""

    # Bumped 1 → 2 in Phase 3 Sub 3.2 to add ``pending_outbox`` /
    # ``failed_outbox``. Bumped 2 → 3 in SA-1371 to add ``pending_timers`` (the
    # durable-timer view). Older snapshots fail the ``min_schema_version``
    # check at load time and the cached projector falls back to full-log
    # replay, self-healing on the next threshold crossing.
    SCHEMA_VERSION: ClassVar[int] = 3

    run_id: str = ""
    workflow_id: str = ""
    status: str = "PENDING"
    started_at: str = ""
    completed_at: str | None = None
    error: str | None = None
    output_data: dict[str, Any] | None = None
    completed_nodes: frozenset[str] = field(default_factory=frozenset)
    skipped_nodes: frozenset[str] = field(default_factory=frozenset)
    child_runs: dict[str, str] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    input_data: dict[str, Any] = field(default_factory=dict)
    # Per-node read-side mirrors of the last ``NodeOutputProduced`` emission
    # — ``output_port`` ("success"/"failure"/branch id) and
    # ``taken_edge_ports`` ({edge_id: port_id}). The dispatcher routes from
    # the live event; these fields exist so the Output panel can show which
    # port/edge was last taken without a second read-side store.
    last_output_ports: dict[str, str] = field(default_factory=dict)
    last_taken_edge_ports: dict[str, dict[str, str]] = field(default_factory=dict)
    # Phase 3 Sub 3.2 — outbox view. ``pending_outbox`` holds entries with an
    # ``OutboxRequested`` but no terminal ``OutboxResolved`` yet; ``failed_outbox``
    # retains entries the dispatcher gave up on. Both keyed by ``outbox_id``.
    pending_outbox: dict[str, dict[str, Any]] = field(default_factory=dict)
    failed_outbox: dict[str, dict[str, Any]] = field(default_factory=dict)
    # SA-1371 — durable-timer view. Timers a run is currently waiting on,
    # keyed by ``timer_id`` → {node_id, kind, fire_at}. ``TimerScheduled``
    # adds one; ``TimerFired`` / ``TimerCancelled`` retire it. Read-side only
    # and bounded by the run's concurrent wait count. The authoritative fire
    # is the leader-gated TimerDispatcher appending ``TimerFired``.
    pending_timers: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ───────────────────────────── reducer (evolve) ─────────────────────

    def apply(self, envelope: EventLogEnvelope) -> "ProjectedRunState":
        """Pure reducer: fold one event-log envelope into a new state.

        Unknown event types return ``self`` unchanged — future phases extend
        the reducer by adding a new branch. This makes it forward-compatible:
        logs containing future event types replay without error, contributing
        nothing to the projected state.
        """
        state = self
        event_type = envelope.event_type
        payload = envelope.payload or {}

        # Every ``DomainEvent`` carries ``workflow_id`` in its payload (set at
        # emit time from the run). Populate ``workflow_id`` the first time a
        # non-empty value is seen and stick with it — workflow_id is immutable
        # per run, so this is idempotent. Prevents WORKFLOW_NOT_FOUND when the
        # log's first envelope is something other than ``RunStatusChanged``.
        if not state.workflow_id:
            candidate = str(payload.get("workflow_id") or "")
            if candidate:
                state = replace(state, workflow_id=candidate)
        if not state.run_id:
            candidate = str(payload.get("run_id") or "")
            if candidate:
                state = replace(state, run_id=candidate)

        # ── run lifecycle ──────────────────────────────────────────────
        # All run-lifecycle transitions arrive as one ``RunStatusChanged``
        # event discriminated by ``payload["status"]``. ``started`` also
        # carries ``input_data`` (folded from the former RunInputReceived).
        if event_type == "RunStatusChanged":
            status = str(payload.get("status") or "").lower()
            # ``error`` is cleared on every transition OUT of a failed state.
            # It used to be set by "failed" and never unset, so once a run had
            # been FAILED the projected error outlived it: a run that later
            # COMPLETED still reported the old string forever, and
            # ``GET /api/v1/runs/{id}`` reads exactly this field
            # (``GetRunStatusUseCase`` -> ``view.error``).
            #
            # Observed on tenant-1 2026-08-25: four of eight runs of a
            # fan-out probe came back ``status=COMPLETED`` carrying
            # ``"Run 'x': expected version 7, found 8"``, while
            # ``/runs/{id}/nodes/status`` for the same run reported no error at
            # all. The aggregate was right — ``OrchestrationEngine._finalize``
            # clears ``run.error`` when it completes a run, deliberately, for
            # this exact case. Only the projection disagreed.
            #
            # The three states below are the ones the aggregate also clears:
            # a run that is starting, resuming (``RerunTaskUseCase`` sets
            # ``run.error = None`` and emits "resumed"), or completing does not
            # carry a failure. CANCELLED is left alone — a cancellation can
            # legitimately record why.
            if status == "started":
                return replace(
                    state,
                    run_id=str(payload.get("run_id") or state.run_id),
                    workflow_id=str(payload.get("workflow_id") or state.workflow_id),
                    status="RUNNING",
                    started_at=str(
                        payload.get("timestamp") or envelope.timestamp or state.started_at
                    ),
                    input_data=dict(payload.get("input_data") or {}),
                    error=None,
                )
            if status == "completed":
                return replace(
                    state,
                    status="COMPLETED",
                    completed_at=str(payload.get("timestamp") or envelope.timestamp),
                    output_data=payload.get("output_data"),
                    error=None,
                )
            if status == "failed":
                return replace(
                    state,
                    status="FAILED",
                    error=str(payload.get("error") or ""),
                    completed_at=str(payload.get("timestamp") or envelope.timestamp),
                )
            if status == "cancelled":
                return replace(
                    state,
                    status="CANCELLED",
                    completed_at=str(payload.get("timestamp") or envelope.timestamp),
                )
            if status == "paused":
                return replace(state, status="PAUSED")
            if status == "resumed":
                return replace(state, status="RUNNING", error=None)
            if status == "suspended":
                return replace(state, status="SUSPENDED")
            if status == "pending":
                return replace(state, status="PENDING")
            return state

        # ── node lifecycle (contribute to node sets) ──────────────────
        if event_type == "NodeDispatched":
            # A manual-mode trigger dispatches a node and moves the run to
            # RUNNING (folded from the former NodeTriggered event); ordinary
            # (re)dispatches contribute nothing to the projected state.
            if str(payload.get("dispatch_reason") or "") == "manual_trigger":
                return replace(state, status="RUNNING")
            return state
        if event_type == "NodeCompleted":
            return _with_completed(state, _extract_node_id(payload))
        if event_type == "NodeFailed":
            # A failed node ends its lifecycle from a projection perspective —
            # the engine also adds it to ``completed_nodes`` for successor
            # processing.
            return _with_completed(state, _extract_node_id(payload))
        if event_type == "NodeCancelled":
            # Cancellation and skip are both terminal. A skip (reason
            # "skipped" / "branch_not_taken") folds into ``skipped_nodes``; an
            # actual cancel folds into ``completed_nodes`` — same as
            # failed/completed from the projection's POV.
            if str(payload.get("reason") or "") in ("skipped", "branch_not_taken"):
                return _with_skipped(state, _extract_node_id(payload))
            return _with_completed(state, _extract_node_id(payload))
        if event_type == "EdgeTraversed":
            # SA-846 cycle execution: a taken edge into a skipped node un-skips
            # it — all skips are flow-derived (branch_not_taken / child-fail),
            # so a later taken edge overrides them. Mirrors the aggregate-side
            # ``skipped_nodes.discard``; a later re-skip arrives as a fresh
            # visit-keyed NodeCancelled.
            return _without_skipped(state, str(payload.get("target_node_id") or ""))

        # ── child workflow tracking ──────────────────────────────────
        if event_type == "ChildRunStarted":
            parent_node_id = str(payload.get("parent_node_id") or "")
            child_run_id = str(payload.get("child_run_id") or "")
            if parent_node_id and child_run_id:
                new_children = dict(state.child_runs)
                new_children[parent_node_id] = child_run_id
                return replace(state, child_runs=new_children)
            return state

        # ── node output → variables (Phase 5.1) ──────────────────────
        # Every state-update write in the engine emits NodeOutputProduced
        # carrying the full write-set (primary keys + name aliases). Applied
        # via dict.update semantics — last write wins per key, matching the
        # live aggregate's last-write behavior.
        if event_type in ("NodeOutputProduced", "NodeDataEdited"):
            # NodeDataEdited (Phase 1a of engine split) shares the same
            # write-set shape — a user-initiated variable edit rather than a
            # handler-produced one, but projection semantics are identical.
            state_updates = payload.get("state_updates") or {}
            output_port = str(payload.get("output_port") or "")
            taken_edge_ports = payload.get("taken_edge_ports") or {}
            node_id = str(payload.get("node_id") or "")

            new_variables = dict(state.variables)
            if state_updates:
                new_variables.update(state_updates)

            new_last_ports = state.last_output_ports
            new_last_edges = state.last_taken_edge_ports
            if node_id and output_port:
                new_last_ports = dict(state.last_output_ports)
                new_last_ports[node_id] = output_port
            if node_id and taken_edge_ports:
                new_last_edges = dict(state.last_taken_edge_ports)
                new_last_edges[node_id] = dict(taken_edge_ports)

            if (
                not state_updates
                and new_last_ports is state.last_output_ports
                and new_last_edges is state.last_taken_edge_ports
            ):
                return state

            return replace(
                state,
                variables=new_variables,
                last_output_ports=new_last_ports,
                last_taken_edge_ports=new_last_edges,
            )

        # ── outbox lifecycle (Phase 3 Sub 3.2) ───────────────────────
        if event_type == "OutboxRequested":
            outbox_id = str(payload.get("outbox_id") or "")
            if not outbox_id:
                return state
            # Idempotent: a redelivered request with the same id leaves the
            # entry's ``requested_at`` pinned to the first observation.
            if outbox_id in state.pending_outbox or outbox_id in state.failed_outbox:
                return state
            pending = dict(state.pending_outbox)
            pending[outbox_id] = {
                "kind": str(payload.get("kind") or ""),
                "requested_at": str(envelope.timestamp or payload.get("timestamp") or ""),
                "idempotency_key": str(payload.get("idempotency_key") or ""),
            }
            return replace(state, pending_outbox=pending)

        if event_type == "OutboxResolved":
            outbox_id = str(payload.get("outbox_id") or "")
            if not outbox_id:
                return state
            pending = dict(state.pending_outbox)
            entry = pending.pop(outbox_id, None)
            outcome = str(payload.get("outcome") or "ok")
            if outcome == "failed" and entry is not None:
                failed = dict(state.failed_outbox)
                failed[outbox_id] = {
                    **entry,
                    "error": payload.get("error"),
                    "resolved_at": str(envelope.timestamp or ""),
                }
                return replace(state, pending_outbox=pending, failed_outbox=failed)
            return replace(state, pending_outbox=pending)

        # ── durable timer view (SA-1371) ──────────────────────────────
        # Track the timers a run is waiting on. Read-side only; the
        # authoritative fire is the TimerDispatcher appending ``TimerFired``.
        if event_type == "TimerScheduled":
            timer_id = str(payload.get("timer_id") or "")
            if not timer_id:
                return state
            pending = dict(state.pending_timers)
            pending[timer_id] = {
                "node_id": str(payload.get("node_id") or ""),
                "kind": str(payload.get("kind") or "delay"),
                "fire_at": str(payload.get("fire_at") or ""),
            }
            return replace(state, pending_timers=pending)
        if event_type in ("TimerFired", "TimerCancelled"):
            # Retire the pending timer. Idempotent: a redelivered fire/cancel
            # for an already-retired timer is a no-op.
            timer_id = str(payload.get("timer_id") or "")
            if not timer_id or timer_id not in state.pending_timers:
                return state
            pending = dict(state.pending_timers)
            pending.pop(timer_id, None)
            return replace(state, pending_timers=pending)

        # ── loop events (Phase 4c.4.2) ─────────────────────────────────
        # ``LoopCompleted`` IS the loop NODE's completion signal (all_done /
        # condition_false / early_exit / empty-collection). No ``NodeCompleted``
        # is ever emitted for the loop node itself — only its BODY nodes get one
        # — so the projector must fold ``LoopCompleted`` into ``completed_nodes``
        # or the loop node stays un-completed forever and the run never
        # finalizes. This was latent until event-driven loops actually advanced
        # past iteration 0: the aggregate-side ``completed_nodes.add(loop)`` was
        # enough for the in-memory read path, but the projector is the
        # authoritative read path on HANA and never saw the mutation.
        if event_type == "LoopCompleted":
            return _with_completed(state, _extract_node_id(payload))
        # The remaining loop lifecycle events carry no projected state today;
        # body NodeCompleted events already drive per-iteration progress.
        if event_type in (
            "LoopStarted",
            "LoopIterationStarted",
            "LoopIterationCompleted",
            "LoopExitRequested",
            "LoopContinueRequested",
        ):
            return state

        # Unknown / no-op event types pass through unchanged.
        return state

    # ───────────────────────────── JSON codec ───────────────────────────

    def to_json(self) -> str:
        """Serialise to the snapshot JSON encoding.

        Frozensets are encoded as sorted JSON arrays (sorted for stable diffs,
        not because the reducer cares about order).
        """
        return json.dumps(
            {
                "run_id": self.run_id,
                "workflow_id": self.workflow_id,
                "status": self.status,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "error": self.error,
                "output_data": self.output_data,
                "completed_nodes": sorted(self.completed_nodes),
                "skipped_nodes": sorted(self.skipped_nodes),
                "child_runs": self.child_runs,
                "variables": self.variables,
                "input_data": self.input_data,
                "pending_outbox": self.pending_outbox,
                "failed_outbox": self.failed_outbox,
                "pending_timers": self.pending_timers,
            },
            separators=(",", ":"),
            sort_keys=False,
        )

    @classmethod
    def from_json(cls, state_json: str) -> "ProjectedRunState":
        """Rehydrate from the snapshot JSON encoding.

        Missing fields take the dataclass default — defensive against
        forward-compat reads where an older snapshot lacks a newer field.
        """
        data: dict[str, Any] = json.loads(state_json)
        return cls(
            run_id=str(data.get("run_id") or ""),
            workflow_id=str(data.get("workflow_id") or ""),
            status=str(data.get("status") or "PENDING"),
            started_at=str(data.get("started_at") or ""),
            completed_at=data.get("completed_at"),
            error=data.get("error"),
            output_data=data.get("output_data"),
            completed_nodes=frozenset(data.get("completed_nodes") or ()),
            skipped_nodes=frozenset(data.get("skipped_nodes") or ()),
            child_runs=dict(data.get("child_runs") or {}),
            variables=dict(data.get("variables") or {}),
            input_data=dict(data.get("input_data") or {}),
            pending_outbox=dict(data.get("pending_outbox") or {}),
            failed_outbox=dict(data.get("failed_outbox") or {}),
            pending_timers=dict(data.get("pending_timers") or {}),
        )

    # ─────────────────────────── shadow parity ──────────────────────────

    def diff(self, other: "ProjectedRunState") -> list[str]:
        """Return field names where this state and ``other`` disagree.

        Empty list means the snapshot+tail path reproduced the full-log replay
        exactly — the only acceptable outcome under ``SNAPSHOT_MODE=shadow``.
        """
        return [
            name
            for name in _TRACKED_FIELDS
            if getattr(self, name) != getattr(other, name)
        ]


# ────────────────────────── internal helpers ────────────────────────


def _extract_node_id(payload: dict[str, Any]) -> str:
    """Both NodeCompleted and BUILTIN-mirrored events carry ``node_id``."""
    return str(payload.get("node_id") or "")


def _with_completed(state: ProjectedRunState, node_id: str) -> ProjectedRunState:
    if not node_id:
        return state
    if node_id in state.completed_nodes:
        return state  # idempotent — set-union is a no-op
    return replace(state, completed_nodes=state.completed_nodes | {node_id})


def _with_skipped(state: ProjectedRunState, node_id: str) -> ProjectedRunState:
    if not node_id:
        return state
    if node_id in state.skipped_nodes:
        return state
    return replace(state, skipped_nodes=state.skipped_nodes | {node_id})


def _without_skipped(state: ProjectedRunState, node_id: str) -> ProjectedRunState:
    if not node_id or node_id not in state.skipped_nodes:
        return state
    return replace(state, skipped_nodes=state.skipped_nodes - {node_id})
