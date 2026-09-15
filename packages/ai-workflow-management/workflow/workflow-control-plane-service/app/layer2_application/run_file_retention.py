"""Retention GC — delete a discarded run's file-backed outputs (SA-1905 E).

Shared by ``TestFullWorkflowUseCase`` and ``ResetTestRunUseCase``: both wipe
``run.context.variables`` **and** ``run.context.loop_state``, which is what makes
the previous cycle's streamed CSV main + recursive artifact files — and a loop's
claim-checked ``items`` and spilled results chunks — unreachable, so both must
delete those files first or every re-run leaks them.

**Why the deletes run before the reset, and must keep doing so.** Uploads are
content-idempotent — ``DataStoreService._upload_csv`` sends
``idempotency_key=_content_key(csv_bytes)`` and folds a content hash into the
stored filename, so byte-identical output dedupes to the *same* ``file_id``. A
re-run that produces identical bytes therefore points at the same object the
previous cycle did. Deleting before anything new is written is what keeps that
safe; deferring this to a background sweeper would delete a live file unless the
sweeper first checks the run's current variables. Do not move it off the
critical path without that guard.

**Why concurrency is safe here** (the Event Mesh publisher pump deliberately
went sequential for ordering — this is the opposite case): the ids come from
``collect_file_ids``, which de-duplicates, and each delete is an independent,
idempotent operation on a distinct file. There is no ordering relationship
between two deletes, so fanning them out changes nothing observable. What it
changes is wall-clock: measured live on tenant-1 (2026-08-06), a ``/test/full``
spent **12.38s of its 13.48s** in this loop — 24 files, strictly one after the
other, ~0.516s each, because a single file-service delete is ~4 object-store
round-trips. Bounded fan-out turns that into ``ceil(n / _DELETE_CONCURRENCY)``
waves.

Best-effort throughout: a missing client is a no-op, ``collect_file_ids`` is
guarded, and a per-file failure is logged and swallowed. Retention must never
fail or hang a reset — both callers ``await`` this with no ``try``/``except`` of
their own. ``asyncio.CancelledError`` is deliberately NOT swallowed (it is a
``BaseException``): a cancelled request should still unwind.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from app.layer2_application.read.node_data_projection import collect_file_ids

#: Max deletes in flight. The file service runs a single web instance and each
#: delete is I/O-bound (head + delete per storage key), so a small fan-out gets
#: nearly all of the win while leaving the service headroom to serve the new
#: cycle's uploads. Raise only with a file-service latency measurement in hand.
_DELETE_CONCURRENCY = 8


class IFileDeleter(Protocol):
    """The one method this module needs from the file-service client."""

    async def delete_file(self, file_id: str) -> dict[str, Any]: ...


def retention_variables(run: Any, view_store: Any = None) -> dict[str, Any]:
    """The ``variables`` tree retention should walk — aggregate first, projector
    view as the fallback.

    Both callers read ``run.context.variables``, which is deserialized from the
    ``CONTEXT`` NCLOB. ``DROP CONTEXT`` (step 5b of
    ``solace/specs/wcp-node-output-storage-redesign.md``) makes that ``{}``, at
    which point ``delete_run_files`` returns early having deleted nothing and
    **every re-run leaks every file** — silently, because retention is
    best-effort and an undeleted file produces no error, no failed task and no
    log line. §14 of that spec names this trap; this is the half of it that
    ``variables`` owns.

    Aggregate FIRST rather than view-first, so behaviour today is byte-identical
    and the fallback only engages once there is nothing in ``CONTEXT`` to find.
    The projector has been authoritative for ``variables`` since Phase 1b
    (``bootstrap.py:319``), so it is the right source — but flipping the
    precedence now would change what gets deleted during the shadow period, and
    a wrong delete here is unrecoverable.
    """
    ctx = getattr(run, "context", None)
    variables = getattr(ctx, "variables", None) if ctx is not None else None
    if variables:
        return variables
    if view_store is None or run is None:
        return {}
    try:
        view = view_store.get(getattr(run, "id", ""))
    except Exception:
        return {}
    return getattr(view, "variables", None) or {} if view is not None else {}


async def delete_run_files(
    client: IFileDeleter | None,
    variables: dict[str, Any] | None,
    logger: Any,
    label: str,
    loop_state: dict[str, Any] | None = None,
) -> None:
    """Delete every file-backed output reachable in ``variables`` — and, when
    given, in ``loop_state``.

    ``loop_state`` is a SECOND root, not a nicety. A loop writes files that
    ``variables`` never references:

    * ``_claim_check_items`` stores an oversized collection as
      ``loop-items-{invocation_id}`` and keeps only the pointer, on
      ``loop_state[node]["items"]``;
    * ``_spill_window`` flushes each results window to
      ``loop-results-{invocation_id}-{n}`` and records the chunk refs on
      ``loop_state[node]["iteration_result_chunks"]``.

    Both are reachable only through ``loop_state``, so walking ``variables``
    alone deletes neither and every re-run of a looping workflow leaks them —
    silently, because retention is best-effort by design and a file nobody asked
    for produces no error. This is the same class of trap the node-output move
    creates on the far side: when data leaves ``variables``, the walk that finds
    it has to follow.

    ``label`` only tags the debug lines so the two callers stay distinguishable
    in the log ("full-run" / "reset").
    """
    if client is None or (not variables and not loop_state):
        return

    try:
        file_ids = collect_file_ids(variables)
        if loop_state:
            # Separate call rather than one walk over a synthetic wrapper: a
            # failure collecting one root must not discard the other's ids.
            for fid in collect_file_ids(loop_state):
                if fid not in file_ids:
                    file_ids.append(fid)
    except Exception as exc:  # pragma: no cover - best-effort GC
        logger.debug("%s retention: collect_file_ids failed: %s", label, exc)
        return

    if not file_ids:
        return

    sem = asyncio.Semaphore(_DELETE_CONCURRENCY)

    async def _delete_one(file_id: str) -> None:
        async with sem:
            try:
                await client.delete_file(file_id)
            except Exception as exc:  # pragma: no cover - best-effort GC
                logger.debug(
                    "%s retention: delete file %s failed: %s", label, file_id, exc,
                )

    # Every task swallows its own failure, so ``gather`` has nothing to
    # propagate; awaiting it here is what preserves "all deletes complete
    # before the reset continues".
    await asyncio.gather(*(_delete_one(fid) for fid in file_ids))
