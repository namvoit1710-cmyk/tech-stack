"""Sweep timed-out child workflow runs and fail them (Temporary). TODO update time out handling to be event-driven and remove this."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from typing import Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.read.run_state import child_runs_of
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer2_application.interfaces.run_context_view_interface import IRunContextViewStore
from app.layer2_application.repositories.run_repository_interface import ListRunsFilter
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.run_save_retry import save_run_with_cas_retry
from app.layer2_application.run_status_reader import read_authoritative_status


_TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)
_ACTIVE_RUNS_PAGE_SIZE = 100


class SweepTimedOutChildRunsUseCase:
    def __init__(
        self,
        logger: ILogger,
        task_repo: Any,
        run_repo: Any,
        orchestration_engine: IOrchestrationEngine,
        workflow_repo: Any = None,
        run_context_view_store: Optional[IRunContextViewStore] = None,
        app_config: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.task_repo = task_repo
        self.run_repo = run_repo
        self.engine = orchestration_engine
        self.workflow_repo = workflow_repo
        self._view_store = run_context_view_store
        self._config = app_config
        self._last_orphan_scan: Optional[datetime] = None

    async def execute(self) -> dict:
        swept = await self._sweep_timeouts()
        orphans = await self._reconcile_orphaned_parents()
        return {"swept": swept, "orphans": orphans}

    async def _sweep_timeouts(self) -> int:
        timed_out_tasks = await self._find_timed_out_tracking_tasks()
        if not timed_out_tasks:
            return 0

        self.logger.info("Sweeping timed-out child workflow tasks", count=len(timed_out_tasks))
        swept = 0

        for tracking_task, timeout_ms in timed_out_tasks:
            try:
                await self._timeout_child_run(tracking_task, timeout_ms)
                swept += 1
            except Exception as exc:
                self.logger.error(
                    "Failed to timeout child run for task",
                    task_id=tracking_task.id,
                    node_id=tracking_task.node_id,
                    error=str(exc),
                )

        return swept

    # ------------------------------------------------------------------
    # Reconciliation — a parent waiting on a child that has already finished.
    # ------------------------------------------------------------------

    async def _reconcile_orphaned_parents(self) -> int:
        """Move parents whose child run is already terminal but whose WORKFLOW
        tracking task never heard about it.

        This is NOT a timeout. It acts on a fact that is either true or not:
        the child run has reached a terminal status and the parent's tracking
        task for that node has not. Whatever caused the notification to be
        skipped — an exception on a path that forgot to call
        ``fail_parent_tracking_task``, a control-plane restart between the two
        writes — the parent is left in a state nothing else will ever move.

        Deliberately NOT covered by ``_find_timed_out_tracking_tasks``: that
        one enumerates RUNNING children, and by definition the child here is
        finished.
        """
        mode = self._reconcile_mode()
        if mode == "off":
            return 0

        now = datetime.now(timezone.utc)
        # Ride the 5s tick, act on its own cadence.
        interval = self._scan_interval_seconds()
        if (
            self._last_orphan_scan is not None
            and (now - self._last_orphan_scan).total_seconds() < interval
        ):
            return 0
        self._last_orphan_scan = now
        grace_ms = self._grace_seconds() * 1000
        found = 0

        for summary in await self._list_running_runs():
            try:
                found += await self._reconcile_one_parent(summary, now, grace_ms, mode)
            except Exception as exc:
                self.logger.error(
                    "Orphan reconciliation failed for run — skipping",
                    run_id=getattr(summary, "id", "?"),
                    error=str(exc),
                )
        return found

    async def _reconcile_one_parent(
        self, summary: Any, now: datetime, grace_ms: float, mode: str,
    ) -> int:
        # The list query returns context-thin summaries (HANA does not parse
        # the context blob), so re-read to reach child_runs + tasks.
        parent = await self.run_repo.aget_by_id(getattr(summary, "id", ""))
        if parent is None:
            return 0
        children = child_runs_of(parent.id, self._view_store, parent)
        if not children:
            return 0

        latest_by_node: dict[str, Any] = {}
        for t in await self.task_repo.alist_by_run(parent.id):
            cur = latest_by_node.get(t.node_id)
            if cur is None or (t.created_at or "") > (cur.created_at or ""):
                latest_by_node[t.node_id] = t

        terminal_values = {
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        }
        found = 0
        for node_id, child_run_id in children.items():
            tracking = latest_by_node.get(node_id)
            if tracking is None or tracking.status in _TERMINAL_TASK_STATUSES:
                continue
            child = await self.run_repo.aget_by_id(child_run_id)
            if child is None:
                continue
            child_status = read_authoritative_status(child, self._view_store)
            if child_status not in terminal_values:
                continue
            # Grace window — never race the normal notification path.
            elapsed = self._elapsed_ms(child.completed_at, now)
            if elapsed is None or elapsed < grace_ms:
                continue

            found += 1
            # The identifying facts go in the MESSAGE, not only in kwargs.
            # ``StandardLogger`` passes kwargs as ``extra=`` and the deployed
            # formatter renders none of it, so the shadow line arrived on
            # tenant-1 as a bare "Orphaned parent tracking task" — a count with
            # nothing to act on, when the detail IS the whole point of shadow
            # mode. The kwargs stay for any structured sink that grows later.
            tracking_status = getattr(
                tracking.status, "value", str(tracking.status),
            )
            self.logger.warning(
                "Orphaned parent tracking task [%s] parent=%s node=%s task=%s "
                "child=%s %s for %.0fs child_error=%s",
                mode, parent.id, node_id, tracking_status,
                child.id, child_status, elapsed / 1000.0,
                (child.error or "(none)")[:160],
                mode=mode,
                parent_run_id=parent.id,
                node_id=node_id,
                child_run_id=child.id,
                child_status=child_status,
                tracking_task_status=tracking_status,
                child_terminal_for_ms=round(elapsed),
                child_error=(child.error or "")[:200],
            )
            if mode != "on" or self.engine is None:
                continue
            if child_status == RunStatus.COMPLETED.value:
                await self.engine._complete_parent_tracking_task(child)
            else:
                await self.engine.fail_parent_tracking_task(child)
        return found

    async def _list_running_runs(self) -> list[Any]:
        """RUNNING runs at any depth — a parent can itself be someone's child.

        Bounded by ``CHILD_ORPHAN_SCAN_LIMIT`` and says so when it truncates.
        """
        limit = self._scan_limit()
        page = await self.run_repo.alist_runs(
            ListRunsFilter(
                statuses=[RunStatus.RUNNING.value], offset=0, limit=limit,
            )
        )
        total = getattr(page, "total", len(page.items))
        if total > len(page.items):
            self.logger.warning(
                "Orphan reconciliation scan truncated — raise "
                "CHILD_ORPHAN_SCAN_LIMIT or the tail is never examined",
                examined=len(page.items),
                running_total=total,
                limit=limit,
            )
        return list(page.items)

    def _reconcile_mode(self) -> str:
        mode = str(
            getattr(self._config, "CHILD_ORPHAN_RECONCILE_MODE", "shadow")
        ).strip().lower()
        return mode if mode in ("off", "shadow", "on") else "shadow"

    def _grace_seconds(self) -> int:
        try:
            return int(getattr(self._config, "CHILD_ORPHAN_GRACE_SECONDS", 60))
        except (TypeError, ValueError):
            return 60

    def _scan_limit(self) -> int:
        try:
            return int(getattr(self._config, "CHILD_ORPHAN_SCAN_LIMIT", 200))
        except (TypeError, ValueError):
            return 200

    def _scan_interval_seconds(self) -> int:
        try:
            return int(getattr(
                self._config, "CHILD_ORPHAN_SCAN_INTERVAL_SECONDS", 60,
            ))
        except (TypeError, ValueError):
            return 60

    # ------------------------------------------------------------------
    # Discovery — child runs whose parent WORKFLOW node has an explicit
    # ``timeout_ms`` that has elapsed.
    # ------------------------------------------------------------------

    async def _find_timed_out_tracking_tasks(self) -> list[tuple[Any, int]]:
        """Return ``(tracking_task, timeout_ms)`` for every active child run
        that has outlived its parent WORKFLOW node's configured ``timeout_ms``.

        Enumerates active child runs (``depth_level > 0``) and re-reads each
        parent via ``aget_by_id``: the list query returns context-thin run
        summaries (HANA does not parse the JSON context blob), so the parent
        must be re-fetched to reach the node config + tracking task. Only
        WORKFLOW nodes with an explicit positive ``timeout_ms`` are swept — a
        child with no configured bound (e.g. one legitimately paused on a
        HUMAN_ACTION/INPUT node) is never touched.

        Per-child failures are isolated: a single malformed run is logged and
        skipped so it can't sink the whole sweep, which ``main.py`` swallows
        (that swallow is what turned the original ``AttributeError`` into a
        silent every-cycle no-op).
        """
        now = datetime.now(timezone.utc)
        timed_out: list[tuple[Any, int]] = []
        for child_run in await self._list_active_child_runs():
            try:
                hit = await self._check_child_timeout(child_run, now)
            except Exception as exc:
                self.logger.error(
                    "Child timeout check failed — skipping run",
                    child_run_id=getattr(child_run, "id", "?"),
                    error=str(exc),
                )
                continue
            if hit is not None:
                timed_out.append(hit)
        return timed_out

    async def _check_child_timeout(
        self, child_run: Any, now: datetime,
    ) -> Optional[tuple[Any, int]]:
        """Resolve a single child run to ``(tracking_task, timeout_ms)`` when
        it has timed out, else ``None``."""
        if not child_run.parent_run_id or not child_run.parent_node_id:
            return None
        parent_run = await self.run_repo.aget_by_id(child_run.parent_run_id)
        if not parent_run:
            return None
        workflow = (
            self.engine.load_workflow_for_run(parent_run) if self.engine else None
        )
        if not workflow:
            return None
        node = workflow.get_node(child_run.parent_node_id)
        if node is None:
            return None
        from app.layer1_domain.node_spec.specs import WorkflowNodeConfig

        timeout_ms = WorkflowNodeConfig.from_node(node).timeout_ms
        if (
            not isinstance(timeout_ms, (int, float))
            or isinstance(timeout_ms, bool)
            or timeout_ms <= 0
        ):
            return None  # no explicit bound (0 = unset) — never sweep
        timeout_ms = int(timeout_ms)

        # Tracking task = the parent's task for this WORKFLOW node. Mirror
        # ``ChildWorkflowCoordinator._find_tracking_task``: first task whose
        # node_id matches (a node can have >1 task after a loop or reset).
        tracking_task = None
        for t in await self.task_repo.alist_by_run(parent_run.id):
            if t.node_id == child_run.parent_node_id:
                tracking_task = t
                break
        if tracking_task is None or tracking_task.status in _TERMINAL_TASK_STATUSES:
            return None

        # Measure from when the child actually started; fall back to the
        # tracking task's timestamps if the child summary lacks started_at.
        started = (
            child_run.started_at
            or tracking_task.started_at
            or tracking_task.created_at
        )
        elapsed_ms = self._elapsed_ms(started, now)
        if elapsed_ms is None or elapsed_ms < timeout_ms:
            return None
        return tracking_task, timeout_ms

    async def _list_active_child_runs(self) -> list[Any]:
        """Page through RUNNING child runs (``depth_level > 0``). Returns
        context-thin run summaries — callers re-fetch the parent for context."""
        collected: list[Any] = []
        offset = 0
        while True:
            page = await self.run_repo.alist_runs(
                ListRunsFilter(
                    statuses=[RunStatus.RUNNING.value],
                    gt={"depth_level": "0"},
                    offset=offset,
                    limit=_ACTIVE_RUNS_PAGE_SIZE,
                )
            )
            collected.extend(page.items)
            offset += len(page.items)
            if not page.items or offset >= page.total:
                break
        return collected

    @staticmethod
    def _elapsed_ms(started_iso: Optional[str], now: datetime) -> Optional[float]:
        """Milliseconds between an ISO-8601 timestamp and ``now``; ``None`` when
        the timestamp is missing or unparseable."""
        if not started_iso:
            return None
        try:
            started = datetime.fromisoformat(started_iso)
        except (ValueError, TypeError):
            return None
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return (now - started).total_seconds() * 1000

    async def _timeout_child_run(self, tracking_task: Any, timeout_ms: int) -> None:
        parent_run = await self.run_repo.aget_by_id(tracking_task.run_id)
        if not parent_run:
            self.logger.warning(
                "Sweep: parent run %s not found for tracking task %s — skipping",
                tracking_task.run_id, tracking_task.id,
            )
            return

        # Find child run ID from parent context
        child_run_id = child_runs_of(
            parent_run.id, self._view_store, parent_run,
        ).get(tracking_task.node_id)
        if not child_run_id:
            self.logger.warning(
                "Sweep: no child run found for node %s in parent run %s — skipping",
                tracking_task.node_id, tracking_task.run_id,
            )
            return

        child_run = await self.run_repo.aget_by_id(child_run_id)
        if not child_run:
            self.logger.warning(
                "Sweep: child run %s not found — skipping", child_run_id,
            )
            return

        # Skip if child already terminal
        terminal_values = {
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        }
        if read_authoritative_status(child_run, self._view_store) in terminal_values:
            return

        self.logger.info(
            "Child workflow timed out",
            child_run_id=child_run.id,
            parent_run_id=parent_run.id,
            node_id=tracking_task.node_id,
            timeout_ms=timeout_ms,
        )

        # Cancel all active tasks in the child run (and cascade to grandchildren)
        await self._cancel_child_tasks(child_run)

        # Fail the child run.
        # Phase 3b.2c: ``RunFailed`` (emitted below) is authoritative
        # for status — projector rebuilds it from the event.
        # The STATUS column is written as well: it is the index behind
        # ``GET /api/v1/runs``, which is SQL over the runs table and
        # cannot replay events. Leaving it alone here had a second cost
        # beyond the stale row — ``_list_active_child_runs`` selects
        # ``STATUS IN ('RUNNING')``, so a child failed by this sweep was
        # re-listed on every subsequent pass and only stopped at the
        # authoritative-status guard above.
        # Phase M.2.2: CAS retry — the mutation is absolute assignment
        # of status + error + completed_at, idempotent under replay.
        error_message = f"Child workflow timed out after {timeout_ms}ms"
        completed_at = datetime.now(timezone.utc).isoformat()
        def _apply_child_failure(fresh_child: Any) -> None:
            fresh_child.status = RunStatus.FAILED
            fresh_child.error = error_message
            fresh_child.completed_at = completed_at
        saved = await save_run_with_cas_retry(
            self.run_repo, child_run.id, _apply_child_failure,
        )
        # Refresh local ref so emit_run_failed + parent fail-propagation
        # observe the persisted version/state.
        if saved is not None:
            child_run = saved

        # Publish run.failed event so Socket.IO notifies the UI
        if self.engine:
            self.engine.emit_run_status(child_run, "failed", error=child_run.error)

        # Let the engine propagate failure to parent (respects on_failure=skip/fail)
        parent_workflow = self.engine.load_workflow_for_run(parent_run) if self.engine else None
        if parent_workflow and self.engine:
            await self.engine.fail_parent_tracking_task(child_run)

    async def _cancel_child_tasks(self, child_run: Any) -> None:
        """Cancel all non-terminal tasks in the child run and cascade to grandchildren.

        Phase 1a engine-split: emit ``NodeCancelled`` / ``RunCancelled``
        BEFORE mutating aggregate state so the projector can rebuild
        the cascade from the event log.
        """
        terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        now = datetime.now(timezone.utc).isoformat()
        all_tasks = await self.task_repo.alist_by_run(child_run.id)
        for task in all_tasks:
            if task.status not in terminal:
                if self.engine:
                    self.engine.emit_node_cancelled(
                        child_run, task=task,
                        cancelled_by="system",
                        reason="parent_timeout",
                    )
                task.status = TaskStatus.CANCELLED
                task.output_metadata = {"reason": "parent_timeout"}
                task.completed_at = now
                await self.task_repo.asave(task)

        # Cascade to grandchild runs
        grandchild_map = child_runs_of(child_run.id, self._view_store, child_run)
        grandchild_terminal_values = {
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        }
        for grandchild_run_id in grandchild_map.values():
            grandchild_run = await self.run_repo.aget_by_id(grandchild_run_id)
            if grandchild_run and read_authoritative_status(grandchild_run, self._view_store) not in grandchild_terminal_values:
                await self._cancel_child_tasks(grandchild_run)
                if self.engine:
                    self.engine.emit_run_status(
                        grandchild_run, "cancelled",
                        cancelled_by="system",
                        reason="parent_timeout",
                    )
                # Phase 3b.2c: ``RunCancelled`` is authoritative for status;
                # only ``completed_at`` persists on the aggregate now.
                # Phase M.2.2: CAS retry — absolute completed_at write.
                gc_completed_at = datetime.now(timezone.utc).isoformat()
                await save_run_with_cas_retry(
                    self.run_repo, grandchild_run.id,
                    lambda fresh: setattr(fresh, "completed_at", gc_completed_at),
                )
