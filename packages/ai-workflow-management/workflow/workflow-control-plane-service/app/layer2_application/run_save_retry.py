"""CAS-retry helper for worker-initiated ``IRunRepository.save`` calls.

Phase M.2.2 — see ``docs/design/phase-m2-optimistic-cas.md``.

User-initiated saves (HTTP handlers) let
``OptimisticConcurrencyError`` propagate so the global FastAPI
handler emits HTTP 409. Worker / background-initiated saves can't
usefully surface 409 (no HTTP caller is waiting), so they retry:
reload, re-apply the mutation, save again. The mutation ``apply``
closure MUST be idempotent under replay — calling it twice on the
same run state must produce the same state.

Typical idempotent patterns in this codebase:
- ``run.context.completed_nodes.add(node_id)``  — set semantics
- ``run.status = RunStatus.FAILED`` — absolute assignment
- ``run.error = "..."`` — absolute assignment
- ``run.completed_at = "..."`` — absolute assignment

Non-idempotent patterns (don't use with this helper):
- ``run.context.variables[k].append(x)``  — list grows on replay
- counter increments
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any, Optional
from collections.abc import Callable

from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer2_application.repositories.run_repository_interface import (
    IRunRepository,
    OptimisticConcurrencyError,
)

_logger = logging.getLogger(__name__)


# Optional process-wide monitor. Set by ``bootstrap`` so every retry
# attempt can emit ``cas_retry_total{use_case=...}`` and exhausted
# retries can emit ``cas_retry_exhausted_total{use_case=...}``. When
# ``None`` (test default) the helper is a no-op observer.
_monitor: Any = None


def set_monitor(monitor: Any) -> None:
    """Wire an ``IMonitor`` instance the helper will emit retry metrics
    through. Called once from bootstrap; subsequent calls overwrite."""
    global _monitor
    _monitor = monitor


# Optional process-wide per-run lock. ``bootstrap`` sets it to
# ``OrchestrationEngine.locked_run_for_write``.
#
# Measured on tenant-1 2026-08-25: without it, a worker-completion callback
# could bump a run's stored VERSION *inside* an orchestration action's
# load->mutate->save window, and that action's trailing ``asave(run)`` died with
# ``expected version N, found N+1`` — 10 of 26 runs of a four-way fan-out. Every
# ``orchestrate_*`` action holds the run's lock for its whole body; every CAS
# writer reaches the aggregate through THIS function; so taking the same lock
# here is what makes the two mutually exclusive.
#
# ``locked_run_for_write`` acquires only when the calling task holds NO run
# lock. That matters: a CAS write is reachable from inside another run's
# section (``ChildWorkflowCoordinator.start`` -> ``check_run_completion(child)``
# -> ``_finalize``), and taking a second run's lock there deadlocks against the
# upward child->parent path. See that method for the full argument.
_lock_provider: Any = None
_warned_no_lock = False


def set_lock_provider(provider: Any) -> None:
    """Wire the per-run lock this helper takes before touching the aggregate.

    ``provider(run_id)`` must return an async context manager, and must be safe
    to enter while another run's lock is held. Called once from ``bootstrap``;
    subsequent calls overwrite.
    """
    global _lock_provider
    _lock_provider = provider


def _emit(metric: str, *, use_case: str, kind: str = "counter") -> None:
    """Best-effort metric emit. Metrics must never break the retry path."""
    mon = _monitor
    if mon is None:
        return
    try:
        if kind == "counter":
            mon.increment(metric, tags={"use_case": use_case})
    except Exception:
        pass


def _observe(metric: str, value: float, *, use_case: str) -> None:
    """Best-effort histogram emit. Same ``use_case`` label key as ``_emit`` —
    a Prometheus label set is frozen on a metric's first observation, so every
    call site of a given metric must pass the SAME keys."""
    mon = _monitor
    if mon is None:
        return
    try:
        mon.histogram(metric, value, tags={"use_case": use_case})
    except Exception:
        pass


async def save_run_with_cas_retry(
    run_repo: IRunRepository,
    run_id: str,
    apply: Callable[[WorkflowRun], None],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.02,
    use_case: str = "unknown",
) -> Optional[WorkflowRun]:
    """Load → apply → save with retry on ``OptimisticConcurrencyError``.

    Returns the saved ``WorkflowRun`` on success, or ``None`` when the
    run does not exist. Raises ``OptimisticConcurrencyError`` if
    ``max_attempts`` retries all fail.

    Backoff is ``base_delay * 2**attempt`` (20ms → 40ms → 80ms by
    default). Low enough for HTTP timeout budgets, high enough to
    let the winner's write settle.

    Uses ``aget_by_id`` / ``asave`` so the SELECT + CAS UPDATE offload
    to the connection manager's executor under ``ASYNC_HANA_ENABLED``,
    keeping the event loop unblocked. Falls back to sync semantics on
    repos that don't implement the async variants (legacy / test
    doubles) via ``getattr`` introspection.

    Takes the run's lock around the whole load->apply->save, so this write
    cannot land inside an orchestration action's own window. Outermost-only —
    see ``_lock_provider``.
    """
    provider = _lock_provider
    if provider is None:
        global _warned_no_lock
        if not _warned_no_lock:
            _warned_no_lock = True
            _logger.warning(
                "save_run_with_cas_retry has no lock provider — run-aggregate "
                "writes are NOT serialised against orchestration actions. "
                "Expected when a use case is constructed directly (unit "
                "tests); in the service it means bootstrap's "
                "set_lock_provider call did not run.",
            )
        return await _load_apply_save(
            run_repo, run_id, apply,
            max_attempts=max_attempts, base_delay=base_delay, use_case=use_case,
        )
    # How long a writer waits for the lock is the whole cost of this fix, and
    # it is invisible from the outside: the caller just sees ``complete_task``
    # take longer. Measure it here rather than argue about it — the wait is
    # bounded by how long an orchestration action holds the run, which is
    # dominated by broker dispatch (0.2-0.4s per successor on tenant-1).
    waited_from = time.monotonic()
    async with provider(run_id):
        _observe(
            "run_save_lock_wait_seconds",
            time.monotonic() - waited_from,
            use_case=use_case,
        )
        return await _load_apply_save(
            run_repo, run_id, apply,
            max_attempts=max_attempts, base_delay=base_delay, use_case=use_case,
        )


async def _load_apply_save(
    run_repo: IRunRepository,
    run_id: str,
    apply: Callable[[WorkflowRun], None],
    *,
    max_attempts: int,
    base_delay: float,
    use_case: str,
) -> Optional[WorkflowRun]:
    """``save_run_with_cas_retry``'s body, with the lock already held.

    The retry loop stays even though the lock now excludes the in-process
    writers: the lock is per-PROCESS, and the CAS check is what still covers a
    second control-plane instance. Belt and braces on purpose — the lock makes
    conflicts rare, CAS keeps them correct.
    """
    # MagicMock-style test doubles auto-create attribute accesses, so
    # ``hasattr`` / ``getattr`` returns a non-None callable that isn't
    # a coroutine. Gate on ``iscoroutinefunction`` to be sure.
    _aget = getattr(run_repo, "aget_by_id", None)
    if _aget is not None and not inspect.iscoroutinefunction(_aget):
        _aget = None
    _asave = getattr(run_repo, "asave", None)
    if _asave is not None and not inspect.iscoroutinefunction(_asave):
        _asave = None
    last_exc: Optional[OptimisticConcurrencyError] = None
    for attempt in range(max_attempts):
        if _aget is not None:
            run = await _aget(run_id)
        else:
            run = run_repo.get_by_id(run_id)
        if run is None:
            return None
        apply(run)
        try:
            if _asave is not None:
                await _asave(run)
            else:
                run_repo.save(run)
            return run
        except OptimisticConcurrencyError as exc:
            last_exc = exc
            _emit("cas_retry_total", use_case=use_case)
            if attempt < max_attempts - 1:
                _logger.info(
                    "CAS conflict on run %s (attempt %d/%d): expected=%d actual=%d — retrying",
                    run_id, attempt + 1, max_attempts, exc.expected, exc.actual,
                )
                await asyncio.sleep(base_delay * (2 ** attempt))
    # All attempts exhausted; re-raise the last conflict so the caller
    # (worker loop) logs and the action fails cleanly.
    assert last_exc is not None
    _emit("cas_retry_exhausted_total", use_case=use_case)
    _logger.error(
        "CAS conflict on run %s: exhausted %d retries — raising",
        run_id, max_attempts,
    )
    raise last_exc
