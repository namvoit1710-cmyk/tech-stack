"""Replay Debugger (SA-1372) — non-mutating, deterministic time-travel over a
run's recorded event log.

The load-bearing insight (verified against this service): the engine is already
event-sourced and ``ProjectedRunState.apply`` is already a *pure* reducer. So the
replay debugger is a **second, read-only consumer** of the append-only log under
a cursor — NOT a re-execution of the engine. It never mutates the run:

  * open a session over the run's log (read-only) — the session stores NO
    events, only a cursor + an incremental fold cache; the timeline ships to
    the client in pages (``list_by_run_page``), never as one full payload,
  * step a cursor over the recorded events ordered by ``sequence``,
  * at any cursor, fold the event prefix through the existing pure reducer for
    the aggregate state — folding is incremental (only the delta since the
    last cursor is fetched/applied; moving backward refolds from zero in
    batches) — and read the single event *at* the cursor for that node's
    resolved input / state_updates / taken_edge_ports,
  * redact secrets (``__credentials`` + a denylist) at the inspector boundary.

This differs from ``rerun_run`` (which resets + re-dispatches — a mutating path).
Replay is side-effect-free: it appends nothing to the log.

Scale: ``sequence`` is contiguous per run (position i == sequence i+1), so page
fetches are (run_id, sequence) range seeks — a 1M-event run costs O(page) per
timeline request and O(delta) per forward step, with O(1) session memory.

ponytail: sessions are held in-memory on this singleton use case (persistence is
a follow-on); child sub-workflow folding is out of v1 scope (single run — the
``get_run_execution_log`` feature already does cross-run merge if needed later).
Authority: 65537.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.run_projection import ProjectedRunState
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.event_log_repository_interface import (
    IEventLogRepository,
)

# Secret-bearing keys redacted at the inspector boundary. ``__credentials`` is
# the engine's own reserved drop key (orchestration_engine.py:298); the rest are
# common secret keys (denylist source: SA-1335 when available).
_REDACT_DENYLIST = {
    "__credentials", "password", "passwd", "api_key", "apikey", "token",
    "access_token", "refresh_token", "secret", "authorization", "private_key",
}
_REDACTED = "***REDACTED***"

_LOOP_EVENTS = {
    "LoopStarted", "LoopIterationStarted", "LoopIterationCompleted",
    "LoopCompleted", "LoopExitRequested", "LoopContinueRequested",
}


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: (_REDACTED if str(k).lower() in _REDACT_DENYLIST else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _redacted_keys(obj: Any, found: set | None = None) -> set:
    found = set() if found is None else found
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in _REDACT_DENYLIST:
                found.add(k)
            else:
                _redacted_keys(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _redacted_keys(v, found)
    return found


# ---------------------------------------------------------------------------
# Result types (dataclasses so the controller's ``result_or_error`` works)
# ---------------------------------------------------------------------------
@dataclass
class ReplayOpenResult:
    replay_session_id: str = ""
    run_id: str = ""
    cursor: int = 0
    total_steps: int = 0
    timeline: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplayStateResult:
    replay_session_id: str = ""
    run_id: str = ""
    cursor: int = 0
    total_steps: int = 0
    node_id: str | None = None
    event: dict[str, Any] = field(default_factory=dict)
    scope_chain: dict[str, Any] = field(default_factory=dict)
    node_io: dict[str, Any] = field(default_factory=dict)
    loop_ctx: dict[str, Any] = field(default_factory=dict)
    completed_nodes: list[str] = field(default_factory=list)
    skipped_nodes: list[str] = field(default_factory=list)
    # Per-node status AT the cursor for the canvas overlay (n8n-style ✓/✗):
    # node_id -> "completed" | "failed" | "skipped" | "running".
    node_status: dict[str, str] = field(default_factory=dict)
    # The current node's failure surfaced inline (n8n-style error), if it failed
    # by this cursor: {"error_code", "error_message"}.
    node_error: dict[str, Any] | None = None
    redacted_keys: list[str] = field(default_factory=list)
    replay_complete: bool = False
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplayTimelineResult:
    replay_session_id: str = ""
    run_id: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 0
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplayCloseResult:
    replay_session_id: str = ""
    closed: bool = False
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ReplayDebuggerUseCase:
    """Singleton (one instance per container) holding in-memory replay sessions.

    Constructor deps are smart-injected by name from the bootstrap container:
    ``logger`` and ``event_log_repo`` are required; ``run_context_view_store`` is
    an optional existence hint.
    """

    def __init__(
        self,
        logger: Any = None,
        event_log_repo: IEventLogRepository | None = None,
        run_context_view_store: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.event_log_repo = event_log_repo
        self.run_context_view_store = run_context_view_store
        # session_id -> {"run_id", "cursor", "total", and the incremental fold
        # cache: "projected", "folded_upto", "node_status", "node_errors"}.
        # No events are stored — O(1) memory per session regardless of log size.
        self._sessions: dict[str, dict[str, Any]] = {}

    # -- lifecycle -------------------------------------------------------
    def open(self, run_id: str, timeline_limit: int = 100) -> ReplayOpenResult:
        total = self.event_log_repo.count_by_run(run_id) if self.event_log_repo else 0
        if not total:
            return ReplayOpenResult(
                run_id=run_id,
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": run_id},
            )
        session_id = uuid.uuid4().hex
        self._sessions[session_id] = {
            "run_id": run_id,
            "cursor": 0,
            # Frozen at open — the session replays a snapshot view even if the
            # log keeps growing (page/fold reads are clamped to this total).
            "total": total,
            "projected": ProjectedRunState(run_id=run_id),
            "folded_upto": 0,
            "node_status": {},
            "node_errors": {},
        }
        first_page = self.event_log_repo.list_by_run_page(
            run_id, 0, max(0, timeline_limit),
        )
        return ReplayOpenResult(
            replay_session_id=session_id,
            run_id=run_id,
            cursor=0,
            total_steps=total,
            timeline=self._timeline_entries(first_page, offset=0),
        )

    def timeline(
        self, session_id: str, offset: int = 0, limit: int = 100,
    ) -> ReplayTimelineResult:
        """One page of the recorded-event timeline (chip labels only — no
        payloads). ``offset`` is 0-based; ``index`` numbering continues the
        open response's (``index = offset + i + 1``)."""
        session = self._sessions.get(session_id)
        if session is None:
            return ReplayTimelineResult(
                replay_session_id=session_id,
                error="replay session not found",
                error_details={"replay_session_id": session_id},
            )
        total = session["total"]
        offset = max(0, min(total, offset))
        limit = max(0, min(limit, total - offset))
        envelopes = self.event_log_repo.list_by_run_page(session["run_id"], offset, limit)
        return ReplayTimelineResult(
            replay_session_id=session_id,
            run_id=session["run_id"],
            items=self._timeline_entries(envelopes, offset=offset),
            total=total,
            offset=offset,
            limit=limit,
        )

    def step(
        self,
        session_id: str,
        op: str = "step",
        n: int = 1,
        node_id: str | None = None,
        index: int | None = None,
    ) -> ReplayStateResult:
        session = self._sessions.get(session_id)
        if session is None:
            return self._no_session(session_id)
        total = session["total"]
        cursor = session["cursor"]
        if op == "run_to" and node_id:
            cursor = self._advance_to_node(session, cursor, node_id)
        elif op == "index" and index is not None:
            cursor = index
        elif op == "pause":
            pass  # cursor unchanged — snapshot at current position
        else:  # "step"
            cursor = cursor + (n if n else 1)
        session["cursor"] = max(0, min(total, cursor))
        return self._state(session_id, session)

    def state(self, session_id: str) -> ReplayStateResult:
        session = self._sessions.get(session_id)
        if session is None:
            return self._no_session(session_id)
        return self._state(session_id, session)

    def close(self, session_id: str) -> ReplayCloseResult:
        existed = self._sessions.pop(session_id, None) is not None
        if not existed:
            return self._no_session_close(session_id)
        return ReplayCloseResult(replay_session_id=session_id, closed=True)

    # -- internals -------------------------------------------------------
    # Events fetched per repo round-trip while folding / scanning. Large enough
    # to amortise query overhead, small enough to keep peak memory flat.
    _FOLD_BATCH = 1000

    @staticmethod
    def _timeline_entries(envelopes: list, offset: int) -> list[dict[str, Any]]:
        return [
            {
                "index": offset + i + 1,
                "sequence": e.sequence,
                "event_type": e.event_type,
                "node_id": (e.payload or {}).get("node_id"),
                "topic": e.topic,
                # n8n-style error highlight in the timeline.
                "is_error": e.event_type == "NodeFailed",
            }
            for i, e in enumerate(envelopes)
        ]

    def _advance_to_node(self, session: dict[str, Any], cursor: int, node_id: str) -> int:
        """Position just past the next event of ``node_id`` at/after ``cursor``,
        scanning the log in batches (never materialising the whole run)."""
        run_id, total = session["run_id"], session["total"]
        pos = max(0, cursor)
        while pos < total:
            batch = self.event_log_repo.list_by_run_page(
                run_id, pos, min(self._FOLD_BATCH, total - pos),
            )
            if not batch:
                break
            for j, env in enumerate(batch):
                if (env.payload or {}).get("node_id") == node_id:
                    return pos + j + 1
            pos += len(batch)
        return total

    @staticmethod
    def _track_node_status(env: Any, status: dict, errors: dict) -> None:
        """Accumulate one event into the per-node canvas overlay (n8n ✓/✗).
        Terminal events win over 'running'."""
        payload = env.payload or {}
        nid = payload.get("node_id")
        if not nid:
            return
        etype = env.event_type
        if etype == "NodeCompleted":
            status[nid] = "completed"
        elif etype == "NodeFailed":
            status[nid] = "failed"
            errors[nid] = {
                "error_code": payload.get("error_code"),
                "error_message": payload.get("error_message"),
            }
        elif etype == "NodeCancelled":
            status[nid] = "skipped" if payload.get("reason") in ("skipped", "branch_not_taken") else "cancelled"
        elif etype in ("NodeInput", "NodeDispatched"):
            status.setdefault(nid, "running")

    def _fold_to(self, session: dict[str, Any], cursor: int) -> None:
        """Advance the session's cached fold (projection + node status) to
        ``cursor``. Forward moves apply only the delta, fetched in batches.
        # ponytail: backward refolds from 0 in batches; add periodic snapshots
        # if profiling says far-backward scrubbing on huge runs matters."""
        if cursor < session["folded_upto"]:
            session["projected"] = ProjectedRunState(run_id=session["run_id"])
            session["folded_upto"] = 0
            session["node_status"] = {}
            session["node_errors"] = {}
        pos = session["folded_upto"]
        projected = session["projected"]
        status, errors = session["node_status"], session["node_errors"]
        while pos < cursor:
            batch = self.event_log_repo.list_by_run_page(
                session["run_id"], pos, min(self._FOLD_BATCH, cursor - pos),
            )
            if not batch:
                break
            for env in batch:
                projected = projected.apply(env)
                self._track_node_status(env, status, errors)
            pos += len(batch)
        session["projected"] = projected
        session["folded_upto"] = pos

    def _state(self, session_id: str, session: dict[str, Any]) -> ReplayStateResult:
        run_id = session["run_id"]
        total = session["total"]
        cursor = max(0, min(total, session["cursor"]))

        # Fold the prefix through the EXISTING pure reducer (never mutates the
        # log) — incrementally, via the session cache.
        self._fold_to(session, cursor)
        projected = session["projected"]

        current_page = (
            self.event_log_repo.list_by_run_page(run_id, cursor - 1, 1)
            if cursor >= 1 else []
        )
        current = current_page[0] if current_page else None
        payload = (current.payload or {}) if current is not None else {}
        node_io = {
            "input": payload.get("resolved_input"),
            "state_updates": payload.get("state_updates"),
            "output": payload.get("output") if payload.get("output") is not None
            else payload.get("output_data"),
            "output_port": payload.get("output_port"),
            "taken_edge_ports": payload.get("taken_edge_ports"),
        }
        loop_ctx: dict[str, Any] = {}
        if current is not None and current.event_type in _LOOP_EVENTS:
            loop_ctx = {
                k: payload.get(k)
                for k in ("iteration", "iter", "total", "scope_stack", "invocation_id", "reason")
                if payload.get(k) is not None
            }

        # Copies — the session cache must not leak by reference into results.
        node_status = dict(session["node_status"])
        current_node = payload.get("node_id")
        node_error = session["node_errors"].get(current_node) if current_node else None
        node_error = dict(node_error) if node_error else None

        redacted = _redacted_keys(payload) | _redacted_keys(projected.variables)
        return ReplayStateResult(
            replay_session_id=session_id,
            run_id=run_id,
            cursor=cursor,
            total_steps=total,
            node_id=current_node,
            event=_redact({
                "sequence": current.sequence if current is not None else 0,
                "event_type": current.event_type if current is not None else "",
                "timestamp": current.timestamp if current is not None else "",
                "payload": payload,
            }),
            scope_chain=_redact(dict(projected.variables)),
            node_io=_redact(node_io),
            loop_ctx=_redact(loop_ctx),
            completed_nodes=sorted(projected.completed_nodes),
            skipped_nodes=sorted(projected.skipped_nodes),
            node_status=node_status,
            node_error=_redact(node_error) if node_error else None,
            redacted_keys=sorted(str(k) for k in redacted),
            replay_complete=(cursor == total),
        )

    def _no_session(self, session_id: str) -> ReplayStateResult:
        return ReplayStateResult(
            replay_session_id=session_id,
            error="replay session not found",
            error_details={"replay_session_id": session_id},
        )

    def _no_session_close(self, session_id: str) -> ReplayCloseResult:
        return ReplayCloseResult(
            replay_session_id=session_id,
            error="replay session not found",
            error_details={"replay_session_id": session_id},
        )
