"""Projector-backed implementation of ``IRunContextViewStore``.

Introduced in Phase 4c.1 and extended in Phase 4c.3 to support all
three Phase 4a-migrated query-side features:

- Phase 4c.1 — built ``ProjectedView`` + ``ProjectorRunContextViewStore``
  around ``rebuild_run_state`` (the Phase 4b.3 reducer). Worked for
  ``get_run_status`` and ``get_run_context`` but not ``get_node_output``
  because the latter reads ``topology`` and ``get_by_root_id``.
- Phase 4c.3 — added a **hybrid fallback model** for the two fields
  the event log alone cannot serve:

  - ``topology`` / ``entry_nodes``: lazy fallback to
    ``workflow_repo.get_by_id(workflow_id)`` →
    ``GraphTopology.from_workflow(workflow)``, cached per view instance. DAG
    info is static per workflow version, so the fallback produces
    byte-identical output to the live store.
  - ``get_by_root_id``: lazy fallback to ``run_repo.get_by_root_id``
    to enumerate parent/child run ids, then ``self.get(run_id)`` for
    each — every individual run's state still comes from the event
    log.

  With these fallbacks wired, all three Phase 4a features produce
  equivalent output from the projector and live stores. Phase 5.2
  event-driven BUILTIN dispatch will eventually replace the run_repo
  fallback with a ``ChildRunStarted``-event-sourced cross-run index.

Still not rebuildable today (no feature reads them):
    - ``input_data`` returns ``{}``.
    - ``workflow_version``, ``parent_run_id``, ``depth_level`` return
      defaults.

Production wiring:
    ``USE_PROJECTOR_READ_MODEL=true`` in ``app_config.py`` flips the
    ``bootstrap.py`` default from ``LiveRunContextViewStore`` to
    ``CachedProjectorRunContextViewStore`` (with both fallback repos
    wired). Default stays ``false`` — operators opt in.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING
from collections.abc import Mapping

from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.projection import (
    ProjectedRunState,
    rebuild_run_state,
)
from app.layer2_application.interfaces.event_log_repository_interface import (
    IEventLogRepository,
)
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextView,
    IRunContextViewStore,
)

if TYPE_CHECKING:
    from app.layer2_application.repositories.run_repository_interface import (
        IRunRepository,
    )
    from app.layer2_application.repositories.workflow_repository_interface import (
        IWorkflowRepository,
    )


class ProjectedView(IRunContextView):
    """Adapter wrapping ``ProjectedRunState`` as an ``IRunContextView``.

    Delegates event-sourced fields (``status``, ``variables``,
    ``completed_nodes``, ``skipped_nodes``, ``child_runs``, etc.) to
    the projected state. For ``topology`` and ``entry_nodes`` — which
    are static workflow metadata and NOT event-sourced — falls back to
    an optional ``IWorkflowRepository`` via
    ``GraphTopology.from_workflow(workflow)`` and memoises the result per
    view instance.

    When ``workflow_repo`` is not supplied (Phase 4c.1 behavior), the
    fallback silently returns empty ``GraphTopology()`` and ``()`` for
    ``entry_nodes`` — preserving backward compatibility with callers
    that opted into the projector store before Phase 4c.3.
    """

    def __init__(
        self,
        projected: ProjectedRunState,
        workflow_repo: IWorkflowRepository | None = None,
    ) -> None:
        self._projected = projected
        self._workflow_repo = workflow_repo
        # Per-instance memoization of the resolved GraphTopology. The view
        # lives for one query; the workflow definition is immutable for
        # the lifetime of the run; this is the simplest cache that
        # guarantees a single repo round-trip per view regardless of
        # how many times the feature reads ``topology`` / ``entry_nodes``.
        self._cached_topology: GraphTopology | None = None

    @property
    def run_id(self) -> str:
        return self._projected.run_id

    @property
    def workflow_id(self) -> str:
        return self._projected.workflow_id

    @property
    def workflow_version(self) -> str:
        # Not in events — callers that need this must stay on the live store.
        return ""

    @property
    def status(self) -> str:
        return self._projected.status

    @property
    def started_at(self) -> str:
        return self._projected.started_at

    @property
    def completed_at(self) -> str | None:
        return self._projected.completed_at

    @property
    def error(self) -> str | None:
        return self._projected.error

    @property
    def output_data(self) -> dict[str, Any] | None:
        return self._projected.output_data

    @property
    def input_data(self) -> dict[str, Any]:
        # Phase 1a of engine split: populated from ``RunInputReceived``
        # events via the projector reducer.
        return dict(self._projected.input_data)

    @property
    def variables(self) -> Mapping[str, Any]:
        return self._projected.variables

    @property
    def last_output_ports(self) -> Mapping[str, str]:
        return self._projected.last_output_ports

    @property
    def last_taken_edge_ports(self) -> Mapping[str, Mapping[str, str]]:
        return self._projected.last_taken_edge_ports

    @property
    def completed_nodes(self) -> frozenset[str]:
        return self._projected.completed_nodes

    @property
    def skipped_nodes(self) -> frozenset[str]:
        return self._projected.skipped_nodes

    @property
    def entry_nodes(self) -> tuple[str, ...]:
        # Delegates to topology so it inherits the workflow_repo
        # fallback automatically when one is wired.
        return tuple(self.topology.entry_nodes)

    @property
    def topology(self) -> GraphTopology:
        # Phase 4c.3: hybrid fallback. DAG info is static per workflow
        # version — not event-sourced — so when a workflow_repo is
        # supplied, lazily reconstruct via TopologyBuilder.prepare and
        # memoise the result for the lifetime of this view instance.
        if self._cached_topology is not None:
            return self._cached_topology
        if self._workflow_repo is None or not self._projected.workflow_id:
            # No fallback wired — preserve the Phase 4c.1 empty-view
            # contract for callers that don't opt in.
            return GraphTopology()
        workflow = self._workflow_repo.get_by_id(self._projected.workflow_id)
        if workflow is None:
            return GraphTopology()
        self._cached_topology = GraphTopology.from_workflow(workflow)
        return self._cached_topology

    @property
    def child_runs(self) -> Mapping[str, str]:
        return self._projected.child_runs

    @property
    def parent_run_id(self) -> str | None:
        # Not reconstructed from events. Phase 5.2 will add it via
        # ChildRunStarted's ``parent_run_id`` already-in-payload field.
        return None

    @property
    def root_run_id(self) -> str:
        # Fall back to run_id when not rebuilt from events.
        return self._projected.run_id

    @property
    def depth_level(self) -> int:
        return 0


class ProjectorRunContextViewStore(IRunContextViewStore):
    """View store that rebuilds state from the event log on each call.

    Unlike ``LiveRunContextViewStore`` (which loads ``WorkflowRun`` from
    the repository), this store replays every envelope for the run via
    ``rebuild_run_state``. It is the concrete demonstration that the
    event log is authoritative for every field a query-side use case
    like ``get_run_status`` / ``get_run_context`` reads today.

    Phase 4c.3 extension: optional ``workflow_repo`` and ``run_repo``
    parameters enable the hybrid fallback model described in the
    module docstring — ``topology`` / ``entry_nodes`` come from the
    workflow repo via ``TopologyBuilder``, and ``get_by_root_id`` uses the
    run repo to discover parent/child run ids before replaying each
    run's event log.

    Returns ``None`` when the event log contains no entries for the
    requested run — matching ``LiveRunContextViewStore``'s "run not
    found" contract.
    """

    def __init__(
        self,
        event_log_repo: IEventLogRepository,
        workflow_repo: IWorkflowRepository | None = None,
        run_repo: IRunRepository | None = None,
    ) -> None:
        self._event_log_repo = event_log_repo
        self._workflow_repo = workflow_repo
        self._run_repo = run_repo

    def get(self, run_id: str) -> IRunContextView | None:
        envelopes = self._event_log_repo.list_by_run(run_id)
        if not envelopes:
            return None
        projected = rebuild_run_state(run_id, self._event_log_repo)
        return ProjectedView(projected, workflow_repo=self._workflow_repo)

    def get_by_root_id(self, root_id: str) -> list[IRunContextView]:
        """Return views for every run rooted at ``root_id``.

        Phase 4c.3: when ``run_repo`` is supplied, the store delegates
        to ``run_repo.get_by_root_id`` to discover run ids and then
        replays each run's event log via ``self.get(run.id)``. This
        is the hybrid fallback — parent/child discovery comes from the
        run repo; per-run state still comes from the event log.

        Without a run_repo wired, raises ``NotImplementedError`` to
        preserve the Phase 4c.1 contract for callers that opted in
        before the fallback was added.
        """
        if self._run_repo is None:
            raise NotImplementedError(
                "ProjectorRunContextViewStore.get_by_root_id requires a "
                "run_repo fallback to discover parent/child run ids. "
                "Pass ``run_repo`` in the constructor (Phase 4c.3) or "
                "use LiveRunContextViewStore for features that need "
                "parent/child run traversal without the fallback."
            )
        runs = self._run_repo.get_by_root_id(root_id)
        views: list[IRunContextView] = []
        for run in runs:
            view = self.get(run.id)
            if view is not None:
                views.append(view)
        return views
