"""The one place that reads run state DROP CONTEXT left empty on the aggregate.

Started as ``child_runs.py``; renamed once the second and third field needed the
same treatment, because two modules answering the same question is how the
answers drift apart.

Read this before adding a caller: the fields below are no longer on the
aggregate, and reading them directly is how six features broke at once.

``89f55519a`` (step 5b of ``solace/specs/wcp-node-output-storage-redesign.md``)
removed ``CONTEXT`` from ``hana_run_repo._DATA_COLUMNS``. The column is not
written any more, so on every run created since 2026-08-08 the aggregate's map
reads back **empty**. Nothing raises: ``ChildWorkflowCoordinator`` still assigns
``run.context.child_runs[node.id]`` and its CAS save still succeeds, it just no
longer persists that field. A silent ``{}`` looked exactly like "this run has no
children", which is why the frontend's run-history drill-in went blank and
``cancel_run`` quietly stopped cascading.

The live source is the **projector**, event-sourced from ``ChildRunStarted``
(``run_projection.py``). ``ChildRunStarted`` carries no ``run_id``, so
``EventLogAppendingPublisher`` files it under ``parent_run_id`` — the parent's
own stream — which is precisely where the parent's projected ``child_runs``
needs it.

The aggregate is still merged in underneath, so runs written BEFORE step 5b
(whose ``CONTEXT`` column still holds data) keep resolving. The projector wins
on conflict: it is the only writer still running.
"""

from __future__ import annotations

from typing import Any


def child_runs_of(
    run_id: str,
    view_store: Any,
    run: Any = None,
) -> dict[str, str]:
    """Return ``{parent_node_id: child_run_id}`` for ``run_id``.

    ``run`` is optional and only supplies the pre-step-5b legacy map; callers
    that already hold the aggregate should pass it so historical runs keep
    working, but nothing depends on it for runs created since.

    A failing view store is **not** swallowed. An empty result here is
    indistinguishable from "no children", and every caller treats that as
    "nothing to cascade to / nothing to show" — which is the exact failure this
    module exists to end. Let it raise and be seen.
    """
    merged: dict[str, str] = {}

    legacy = getattr(getattr(run, "context", None), "child_runs", None)
    if isinstance(legacy, dict):
        merged.update(
            {str(k): str(v) for k, v in legacy.items() if k and v}
        )

    if view_store is not None and run_id:
        view = view_store.get(run_id)
        projected = getattr(view, "child_runs", None)
        if projected:
            merged.update(
                {str(k): str(v) for k, v in dict(projected).items() if k and v}
            )

    return merged


def skipped_nodes_of(
    run_id: str,
    view_store: Any,
    run: Any = None,
) -> set[str]:
    """Return the set of node ids this run skipped.

    Same story as ``child_runs_of``: ``run.context.skipped_nodes`` rode
    ``CONTEXT`` and now reads back empty, so a skipped node renders as if it
    never ran. The projector folds the set from ``NodeCancelled``.

    Union rather than either-or: the engine still mutates the aggregate copy
    in-frame (``successor_processor`` un-skips a revisited node synchronously),
    and that mutation is younger than the view whenever the event has not been
    folded yet. Neither side alone is complete — the aggregate is empty after a
    reload, the view lags inside a call frame.
    """
    merged: set[str] = set()

    local = getattr(getattr(run, "context", None), "skipped_nodes", None)
    if local:
        merged |= {str(n) for n in local}

    if view_store is not None and run_id:
        view = view_store.get(run_id)
        projected = getattr(view, "skipped_nodes", None)
        if projected:
            merged |= {str(n) for n in projected}

    return merged


def variables_of(
    run_id: str,
    view_store: Any,
    run: Any = None,
) -> dict[str, Any]:
    """Return the run's ``variables`` tree.

    The projector has been authoritative for ``variables`` since Phase 1b, and
    since DROP CONTEXT the aggregate carries nothing at all after a reload.

    The VIEW wins on conflict here — the opposite of ``child_runs_of``, and
    deliberately. ``child_runs`` is append-only, so merging can only add a link;
    ``variables`` entries are overwritten in place (a node re-run replaces its
    output), and preferring a stale aggregate value would resurrect the previous
    visit's output. The aggregate is kept only to fill keys the view lacks,
    which is what an in-frame seed before the first publish looks like.
    """
    merged: dict[str, Any] = {}

    local = getattr(getattr(run, "context", None), "variables", None)
    if isinstance(local, dict):
        merged.update(local)

    if view_store is not None and run_id:
        view = view_store.get(run_id)
        projected = getattr(view, "variables", None)
        if projected:
            merged.update(dict(projected))

    return merged


def completed_nodes_of(
    run_id: str,
    view_store: Any,
    run: Any = None,
) -> set[str]:
    """Return the set of node ids this run has completed.

    The symmetric twin of ``skipped_nodes_of``, and unioned for the same
    reason: the engine adds the just-completed node to the aggregate copy
    synchronously (``process_successors``), which is younger than the view until
    ``NodeCompleted`` folds — while after a reload the aggregate has nothing and
    only the view does.
    """
    merged: set[str] = set()

    local = getattr(getattr(run, "context", None), "completed_nodes", None)
    if local:
        merged |= {str(n) for n in local}

    if view_store is not None and run_id:
        view = view_store.get(run_id)
        projected = getattr(view, "completed_nodes", None)
        if projected:
            merged |= {str(n) for n in projected}

    return merged
