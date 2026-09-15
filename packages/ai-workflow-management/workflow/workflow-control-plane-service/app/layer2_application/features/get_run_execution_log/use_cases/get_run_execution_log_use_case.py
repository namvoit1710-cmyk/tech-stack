"""Full execution event log for a selected run.

Returns the complete, ordered stream of domain events recorded for a run
during its execution, plus a small summary block. The event log
(``IEventLogRepository``) is the write-ahead record of every event the
engine emits, keyed per run; this feature surfaces it as a read-only,
filterable, paginated list.

Child (sub-workflow) runs are folded in by default: each child run is a
real run with its own ``run_id`` and its own per-run event stream, so we
discover the subtree via the projected ``child_runs`` map (populated from
``ChildRunStarted``) and merge every run's log into one timeline ordered
by ``(timestamp, run_id, sequence)`` — per-run ``sequence`` cannot order
across runs, so ISO-8601 ``timestamp`` is the cross-run key.

Design note — ``since_sequence`` with child runs: ``sequence`` is a
per-run counter, so a single threshold is exact only for a single run.
It is applied per-run (``event.sequence > N`` within each run's own
stream). For precise incremental tailing call with
``include_children=False`` so it maps cleanly to one run's sequence space.
"""

from __future__ import annotations

import heapq
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.event_log_repository_interface import (
    IEventLogRepository,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)

# ``NodeCancelled.reason`` values that mean "the node was skipped" rather
# than actively cancelled — used to derive the skipped-node count.
_SKIP_REASONS = {"skipped", "branch_not_taken"}


# ---------------------------------------------------------------------------
# Command / Result types
# ---------------------------------------------------------------------------

@dataclass
class GetRunExecutionLogCommand:
    run_id: str = ""
    # Optional filters / paging — all applied to the merged, ordered stream.
    event_type: str | None = None
    node_id: str | None = None
    since_sequence: int | None = None
    limit: int | None = None
    offset: int = 0
    # Fold in events from nested sub-workflow runs this run spawned.
    include_children: bool = True


@dataclass
class GetRunExecutionLogResult:
    summary: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0                       # count AFTER filters, BEFORE paging
    limit: int | None = None
    offset: int = 0
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class GetRunExecutionLogUseCase:
    def __init__(
        self,
        logger: ILogger,
        event_log_repo: IEventLogRepository,
        run_context_view_store: IRunContextViewStore,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.event_log_repo = event_log_repo
        self.run_context_view_store = run_context_view_store

    async def execute(
        self, command: GetRunExecutionLogCommand,
    ) -> GetRunExecutionLogResult:
        root_view = self.run_context_view_store.get(command.run_id)
        if not root_view:
            return GetRunExecutionLogResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )

        # 1. Discover the subtree (selected run + descendants), depth relative
        #    to the selected run (selected = 0).
        subtree = self._discover_subtree(
            command.run_id, root_view, command.include_children,
        )

        # 2. Read each run's full log and merge into one ordered timeline.
        #    ``list_by_run`` is authoritative WITHIN a run (append/sequence
        #    order), so each per-run stream is kept as-is and we k-way merge
        #    across runs on the PARSED timestamp. This preserves within-run
        #    order even if clocks are non-monotonic and avoids the
        #    lexicographic ``Z`` vs ``+00:00`` mis-sort a raw string compare
        #    would cause. (Single run / include_children=False → the run's
        #    own order, untouched.)
        per_run_streams = [
            [(envelope, depth) for envelope in self.event_log_repo.list_by_run(run_id)]
            for run_id, depth, _view in subtree
        ]
        merged: list[tuple[Any, int]] = list(
            heapq.merge(*per_run_streams, key=lambda pair: self._ts_key(pair[0].timestamp)),
        )

        all_events = [
            self._to_dict(envelope, depth, index)
            for index, (envelope, depth) in enumerate(merged)
        ]

        # 3. Summary over the FULL merged set + the root view (robust for
        #    empty logs — status/identity always come from the view).
        summary = self._build_summary(root_view, subtree, merged, len(all_events))

        # 4. Filter (since_sequence → event_type → node_id).
        filtered = all_events
        if command.since_sequence is not None:
            filtered = [e for e in filtered if e["sequence"] > command.since_sequence]
        if command.event_type:
            filtered = [e for e in filtered if e["event_type"] == command.event_type]
        if command.node_id:
            filtered = [
                e for e in filtered
                if (e["payload"] or {}).get("node_id") == command.node_id
            ]
        total = len(filtered)

        # 5. Paginate (limit=None → return everything from offset).
        offset = max(0, command.offset or 0)
        if command.limit is not None:
            page = filtered[offset:offset + max(0, command.limit)]
        else:
            page = filtered[offset:]

        return GetRunExecutionLogResult(
            summary=summary,
            events=page,
            total=total,
            limit=command.limit,
            offset=offset,
        )

    # -- helpers ------------------------------------------------------------

    def _discover_subtree(
        self, root_id: str, root_view: Any, include_children: bool,
    ) -> list[tuple[str, int, Any]]:
        """BFS over ``child_runs`` from the selected run.

        Returns ``(run_id, depth, view)`` triples; ``depth`` is relative to
        the selected run. Children are only walked when ``include_children``.
        """
        subtree: list[tuple[str, int, Any]] = []
        seen: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(root_id, 0)])
        while queue:
            run_id, depth = queue.popleft()
            if run_id in seen:
                continue
            seen.add(run_id)
            view = root_view if run_id == root_id else self.run_context_view_store.get(run_id)
            if view is None:
                continue
            subtree.append((run_id, depth, view))
            if include_children:
                for child_id in view.child_runs.values():
                    if child_id and child_id not in seen:
                        queue.append((child_id, depth + 1))
        return subtree

    @staticmethod
    def _ts_key(timestamp: Any) -> datetime:
        """Parse an ISO-8601 timestamp into a comparable, tz-aware datetime.

        Normalises trailing ``Z`` and naive values to UTC so the cross-run
        merge compares instants, not raw strings. Unparseable timestamps
        sort first (they predate any real event ordering signal we have).
        """
        try:
            dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    @staticmethod
    def _to_dict(envelope: Any, depth: int, index: int) -> dict[str, Any]:
        return {
            "index": index,
            "run_id": envelope.run_id,
            "depth": depth,
            "sequence": envelope.sequence,
            "event_id": envelope.event_id,
            "event_type": envelope.event_type,
            "topic": envelope.topic,
            "timestamp": envelope.timestamp,
            "causation_id": envelope.causation_id,
            "payload": envelope.payload,
        }

    @staticmethod
    def _build_summary(
        root_view: Any,
        subtree: list[tuple[str, int, Any]],
        merged: list[tuple[Any, int]],
        total_events: int,
    ) -> dict[str, Any]:
        # Distinct ``(run_id, node_id)`` per category — avoids double-counting
        # loop bodies / retries that re-emit the same event for one node.
        completed: set[tuple[str, str]] = set()
        failed: set[tuple[str, str]] = set()
        skipped: set[tuple[str, str]] = set()
        for envelope, _depth in merged:
            payload = envelope.payload or {}
            node_id = payload.get("node_id")
            if not node_id:
                continue
            key = (envelope.run_id, node_id)
            if envelope.event_type == "NodeCompleted":
                completed.add(key)
            elif envelope.event_type == "NodeFailed":
                failed.add(key)
            elif (
                envelope.event_type == "NodeCancelled"
                and payload.get("reason") in _SKIP_REASONS
            ):
                skipped.add(key)
        return {
            "run_id": root_view.run_id,
            "workflow_id": root_view.workflow_id,
            "status": root_view.status,
            "started_at": root_view.started_at,
            "completed_at": root_view.completed_at,
            "run_ids": [run_id for run_id, _depth, _view in subtree],
            "total_runs": len(subtree),
            "total_events": total_events,
            "node_counts": {
                "completed": len(completed),
                "failed": len(failed),
                "skipped": len(skipped),
            },
        }
